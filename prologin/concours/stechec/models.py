import contextlib
import itertools
import json
import os
import pprint
import re
import tarfile
import tempfile

from django.conf import settings
from django.db import connection, models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django_prometheus.models import ExportModelOperationsMixin
from enum import IntEnum

import prologin.rpc.client
from prologin.concours.stechec.languages import LANGUAGES, PYGMENTS_LEXERS

stripper_re = re.compile(r'\033\[.*?m')


def strip_ansi_codes(t):
    return stripper_re.sub('', t)


def rec_truncate(obj, maxlen=79):
    if isinstance(obj, str) and len(obj) > maxlen:
        return "..." + strip_ansi_codes(obj[-maxlen:])
    if isinstance(obj, bytes) and len(obj) > maxlen:
        return b"..." + obj[-maxlen:]
    if isinstance(obj, dict):
        return {
            rec_truncate(k, maxlen): rec_truncate(v, maxlen)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [rec_truncate(e, maxlen) for e in obj]
    return obj


def bulk_create_return_ids(model, objs):
    """Wrapper for Model.objects.bulk_create() always returning created ids."""
    # Returning created ids requires PostgreSQL
    if connection.vendor == 'sqlite':
        for m in objs:
            m.save()
        return objs
    else:
        return model.objects.bulk_create(objs)


class Map(ExportModelOperationsMixin('map'), models.Model):
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='maps',
        null=True,
        on_delete=models.CASCADE,
        verbose_name="auteur",
    )
    name = models.CharField("nom", max_length=100)
    official = models.BooleanField("officielle", default=False)
    ts = models.DateTimeField("date", auto_now_add=True)
    contents = models.TextField("contenu")

    def get_absolute_url(self):
        return reverse("map-detail", kwargs={"pk": self.id})

    def __str__(self):
        return "%s, de %s%s" % (
            self.name,
            self.author.username,
            " (officielle)" if self.official else "",
        )

    class Meta:
        ordering = ["-official", "-ts"]
        verbose_name = "carte"
        verbose_name_plural = "cartes"


class Champion(ExportModelOperationsMixin('champion'), models.Model):
    SOURCES_FILENAME = 'champion.tgz'
    STATUS_CHOICES = (
        ('new', 'En attente de compilation'),
        ('pending', 'En cours de compilation'),
        ('ready', 'Compilé et prêt'),
        ('error', 'Erreur de compilation'),
        ('failed', 'Compilation abandonnée'),
    )

    name = models.CharField("nom", max_length=100, unique=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='champions',
        on_delete=models.CASCADE,
        verbose_name="auteur",
    )
    status = models.CharField(
        "statut", choices=STATUS_CHOICES, max_length=100, default="new"
    )
    deleted = models.BooleanField("supprimé", default=False)
    comment = models.TextField("commentaire", blank=True)
    ts = models.DateTimeField("date", auto_now_add=True)

    @property
    def directory(self):
        if self.id is None:
            raise RuntimeError(
                "Champion must be saved before accessing its directory"
            )
        contest_dir = settings.STECHEC_ROOT / settings.STECHEC_CONTEST
        return contest_dir / "champions" / self.author.username / str(self.id)

    @property
    def sources_path(self):
        return self.directory / self.SOURCES_FILENAME

    @property
    def sources(self):
        return self.sources_path.open('rb')

    @sources.setter
    def sources(self, uploaded_file):
        if uploaded_file is None:
            return
        self.directory.mkdir(parents=True)
        with self.sources_path.open('wb') as fp:
            for chunk in uploaded_file.chunks():
                fp.write(chunk)

    @property
    def compilation_log(self):
        log_path = self.directory / 'compilation.log'
        try:
            return log_path.read_text().strip()
        except FileNotFoundError:
            return "Log de compilation introuvable."
        except Exception as e:
            return str(e)

    def workernode_result(self):
        try:
            return json.loads(
                (
                    self.directory / 'compilation-workernode-result.json'
                ).read_text()
            )
        except FileNotFoundError:
            return None

    def workernode_result_printable(self):
        result = self.workernode_result()
        if result is None:
            return "Résultat Workernode introuvable."
        rep = rec_truncate(result, maxlen=2000)
        return pprint.pformat(rep, width=120)

    @contextlib.contextmanager
    def _extract_sources(self):
        with tempfile.TemporaryDirectory(prefix='champion-src-') as tmpd:
            with self.sources as tarball:
                with tarfile.open(fileobj=tarball, mode='r:gz') as tar:
                    def is_within_directory(directory, target):
                        
                        abs_directory = os.path.abspath(directory)
                        abs_target = os.path.abspath(target)
                    
                        prefix = os.path.commonprefix([abs_directory, abs_target])
                        
                        return prefix == abs_directory
                    
                    def safe_extract(tar, path=".", members=None, *, numeric_owner=False):
                    
                        for member in tar.getmembers():
                            member_path = os.path.join(path, member.name)
                            if not is_within_directory(path, member_path):
                                raise Exception("Attempted Path Traversal in Tar File")
                    
                        tar.extractall(path, members, numeric_owner=numeric_owner) 
                        
                    
                    safe_extract(tar, tmpd)
                    yield tmpd

    @cached_property
    def language(self):
        with self._extract_sources() as tmpd:
            with open(os.path.join(tmpd, '_lang')) as langf:
                lang_code = langf.read().strip()
        return {
            'code': lang_code,
            **LANGUAGES.get(lang_code, {}),
            'lexer': PYGMENTS_LEXERS.get(lang_code, 'text'),
        }

    @cached_property
    def source_contents(self):
        '''Returns a dictionary file_name -> content'''
        ext_whitelist = set(
            itertools.chain(*(lang['exts'] for lang in LANGUAGES.values()))
        )
        file_blacklist = [
            'interface',
            'api',
            'constant',
            'ffi',
            'capi',
        ]
        sources = {}
        with self._extract_sources() as tmpd:
            for entry in os.scandir(tmpd):
                if not entry.is_file():
                    continue
                if not any(
                    entry.name.lower().endswith(ext) for ext in ext_whitelist
                ):
                    continue
                if any(
                    entry.name.lower().startswith(bf) for bf in file_blacklist
                ):
                    continue
                with open(entry.path) as f:
                    sources[entry.name] = f.read()
        return sources

    @cached_property
    def sloc(self):
        sources = self.source_contents
        return sum(
            len(list(filter(bool, f.split('\n')))) for f in sources.values()
        )

    def get_absolute_url(self):
        return reverse('champion-detail', kwargs={'pk': self.id})

    def get_delete_url(self):
        return reverse('champion-delete', kwargs={'pk': self.id})

    def __str__(self):
        return "%s (de %s)" % (self.name, self.author)

    class Meta:
        ordering = ['-ts']
        verbose_name = "champion"
        verbose_name_plural = "champions"


class Tournament(ExportModelOperationsMixin('tournament'), models.Model):
    name = models.CharField("nom", max_length=100)
    ts = models.DateTimeField("date", auto_now_add=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='tournaments',
        on_delete=models.CASCADE,
        verbose_name="créé par",
    )
    players = models.ManyToManyField(
        Champion,
        verbose_name="participants",
        related_name='tournaments',
        through='TournamentPlayer',
    )
    maps = models.ManyToManyField(
        Map,
        verbose_name="maps",
        related_name='tournaments',
        through='TournamentMap',
    )
    visible = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("tournament-detail", kwargs={"pk": self.id})

    class Meta:
        ordering = ['-ts']
        verbose_name = "tournoi"
        verbose_name_plural = "tournois"


class TournamentPlayer(
    ExportModelOperationsMixin('tournament_player'), models.Model
):
    champion = models.ForeignKey(
        Champion,
        on_delete=models.CASCADE,
        related_name='tournamentplayers',
        verbose_name="champion",
    )
    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE,
        related_name='tournamentplayers',
        verbose_name="tournoi",
    )
    score = models.IntegerField("score", default=0)

    def __str__(self):
        return "%s pour tournoi %s" % (self.champion, self.tournament)

    class Meta:
        ordering = ["-tournament", "-score"]
        verbose_name = "participant au tournoi"
        verbose_name_plural = "participants au tournoi"


class TournamentPlayerCorrection(models.Model):
    player = models.OneToOneField(
        TournamentPlayer,
        on_delete=models.CASCADE,
        related_name='correction',
        verbose_name="joueur",
    )
    comment = models.TextField(verbose_name="commentaire")
    include_jury_report = models.BooleanField(
        default=False, verbose_name="inclure dans le rapport de jury"
    )

    class Meta:
        verbose_name = "correction du joueur"
        verbose_name_plural = "corrections des joueurs"


class TournamentMap(
    ExportModelOperationsMixin('tournament_map'), models.Model
):
    map = models.ForeignKey(
        Map, on_delete=models.CASCADE, verbose_name="carte"
    )
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, verbose_name="tournoi"
    )

    def __str__(self):
        return "%s pour tournoi %s" % (self.map.name, self.tournament.name)

    class Meta:
        ordering = ["-tournament"]
        verbose_name = "carte utilisée dans le tournoi"
        verbose_name_plural = "cartes utilisées dans le tournoi"


class MatchPriority(IntEnum):
    NOW = 1000
    INTERACTIVE = 700
    DEFAULT = 500
    TOURNAMENT = 300
    BEST_EFFORT = 0


class Match(ExportModelOperationsMixin('match'), models.Model):
    STATUS_CHOICES = (
        ('creating', 'En cours de création'),
        ('new', 'En attente de lancement'),
        ('pending', 'En cours de calcul'),
        ('done', 'Terminé'),
        ('failed', 'Échec'),
    )

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='matches',
        on_delete=models.CASCADE,
        verbose_name="lancé par",
    )
    status = models.CharField(
        "statut", choices=STATUS_CHOICES, max_length=100, default="creating"
    )
    tournament = models.ForeignKey(
        Tournament,
        related_name='matches',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        verbose_name="tournoi",
    )
    players = models.ManyToManyField(
        Champion,
        verbose_name="participants",
        related_name='matches',
        through='MatchPlayer',
    )
    ts = models.DateTimeField("date", default=timezone.now)
    map = models.ForeignKey(
        Map,
        null=True,
        blank=True,
        related_name='matches',
        on_delete=models.CASCADE,
        verbose_name="carte",
    )
    priority = models.IntegerField(
        "priority",
        default=MatchPriority.DEFAULT,
        help_text="Match scheduling priority, higher is faster.",
    )

    @property
    def directory(self):
        hi_id, low_id = divmod(self.id, 1000)
        return (
            settings.STECHEC_ROOT
            / settings.STECHEC_CONTEST
            / "matches"
            / "{:03d}".format(hi_id)
            / "{:03d}".format(low_id)
        )

    def log(self, out='stdout'):
        log_path = self.directory / 'server.{}.log'.format(out)
        try:
            return strip_ansi_codes(log_path.open().read()).strip()
        except FileNotFoundError:
            return "Log de match introuvable."
        except Exception as e:
            return str(e)

    @property
    def log_out(self):
        return self.log('stdout')

    @property
    def log_err(self):
        return self.log('stderr')

    @property
    def dump_path(self):
        return self.directory / 'dump.json.gz'

    @property
    def dump(self):
        try:
            return self.dump_path.open('rb').read()
        except Exception:
            pass

    @property
    def dump_url(self):
        return reverse('match-dump', kwargs={'pk': self.id})

    @property
    def replay_path(self):
        return self.directory / 'replay.gz'

    @property
    def replay(self):
        try:
            return self.replay_path.open('rb').read()
        except Exception:
            pass

    @property
    def replay_url(self):
        return reverse('match-replay', kwargs={'pk': self.id})

    @property
    def is_done(self):
        return self.status == 'done'

    def get_absolute_url(self):
        return reverse('match-detail', kwargs={'pk': self.id})

    def workernode_result(self):
        try:
            return json.loads(
                (self.directory / 'server-workernode-result.json').read_text()
            )
        except FileNotFoundError:
            return None

    def workernode_result_printable(self):
        result = self.workernode_result()
        if result is None:
            return "Résultat Workernode introuvable."
        rep = rec_truncate(result, maxlen=2000)
        return pprint.pformat(rep, width=120)

    def __str__(self):
        return "%s (par %s)" % (self.ts, self.author)

    class Meta:
        ordering = ["-ts"]
        verbose_name = "match"
        verbose_name_plural = "matches"

    @classmethod
    def launch_bulk(cls, matches, priority=MatchPriority.DEFAULT):
        """Launch matches in bulk.

        Args:
            matches (iterable): iterable of dictionaries representing the
                individual matches to launch. Each dictionary has the following
                keys:

                - **author**: the creator of the match
                - **tournament**: optionally, the tournament in which the match
                  is started
                - **map**: optionally, the map of the match if the game is
                  using maps
                - **champions**: an ordered list of Champions fighting in the
                  match

        Returns:
            The list of launched matches.
        """
        ts = timezone.now()

        with transaction.atomic():
            # Bulk create all Match objects
            match_objs = []
            for match in matches:
                m = Match()
                m.author = match['author']
                m.ts = ts
                if 'tournament' in match:
                    m.tournament = match['tournament']
                if 'map' in match:
                    m.map = match['map']
                m.priority = priority
                match_objs.append(m)
            created_matches = bulk_create_return_ids(Match, match_objs)

            # Bulk create all MatchPlayer objects
            player_objs = []
            for i, m in enumerate(created_matches):
                for c in matches[i]['champions']:
                    player_objs.append(MatchPlayer(champion=c, match=m))
            bulk_create_return_ids(MatchPlayer, player_objs)

            # Update all matches to set them as 'new' (i.e schedule them)
            new_matches_id = [m.id for m in created_matches]
            qs = Match.objects.filter(id__in=new_matches_id)
            qs.update(status='new')
            return qs


class MatchPlayer(ExportModelOperationsMixin('match_player'), models.Model):
    champion = models.ForeignKey(
        Champion,
        related_name='matchplayers',
        verbose_name="champion",
        on_delete=models.CASCADE,
    )
    match = models.ForeignKey(
        Match,
        related_name='matchplayers',
        verbose_name="match",
        on_delete=models.CASCADE,
    )
    score = models.IntegerField(default=0, verbose_name="score")
    has_timeout = models.BooleanField(
        default=False, verbose_name="has timeout"
    )

    @property
    def log_path(self):
        return self.match.directory / "log-champ-{}-{}.log".format(
            self.id, self.champion.id
        )

    @property
    def log(self):
        try:
            return strip_ansi_codes(self.log_path.open().read()).strip()
        except FileNotFoundError:
            return "Log de match introuvable."
        except Exception as e:
            return str(e)

    def __str__(self):
        return "%s pour match %s" % (self.champion, self.match)

    class Meta:
        ordering = ["-match"]
        verbose_name = "participant à un match"
        verbose_name_plural = "participants à un match"


def master_status():
    rpc = prologin.rpc.client.SyncClient(
        settings.STECHEC_MASTER, secret=settings.STECHEC_MASTER_SECRET
    )
    return rpc.status()
