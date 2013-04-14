# -*- encoding: utf-8 -*-
# Copyright (c) 2013 Pierre Bourdon <pierre.bourdon@prologin.org>
# Copyright (c) 2013 Association Prologin <info@prologin.org>
#
# Prologin-SADM is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Prologin-SADM is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Prologin-SADM.  If not, see <http://www.gnu.org/licenses/>.

"""Client library for the Machine Database (MDB)."""

import json
import logging
import requests
import urllib.parse

_DEFAULT_URL = 'http://mdb'


class _MDBClient:
    """Internal MDB client class. Use mdb.connect() to create a MDB client
    object.
    """

    def __init__(self, url, auth):
        self.url = url
        self.auth = auth

    def _submit_rpc(self, path, data=None):
        """Sends a RPC to the mdb. Passes authentication data if available.

        Args:
          path: Server path of the RPC (from self.url).
          data: Optional data dictionary to POST.
        """
        url = urllib.parse.urljoin(self.url, path)
        params = { 'data': data }
        if self.auth is not None:
            params['auth'] = self.auth
        r = requests.post(url, **params)
        return r.json()

    def query(self, **kwargs):
        """Query the MDB using the Django query syntax. The possible fields
        are:
          hostname: the machine name and any of its aliases
          ip: the machine IP address
          mac: the machine MAC address
          rfs: nearest root file server
          hfs: nearest home file server
          mtype: machine type, either user/orga/cluster/service
          room: physical room location, either pasteur/masters/cluster/other
        """
        fields = { 'hostname', 'ip', 'mac', 'rfs', 'hfs', 'mtype', 'room' }
        for q in kwargs:
            base = q.split('_')[0]
            if base not in fields:
                raise ValueError('%r is not a valid query argument' % q)
        try:
            post_data = json.dumps(kwargs)
        except TypeError:
            raise ValueError('non serializable argument type')
        return self._submit_rpc('/query', data=kwargs)


def connect(url=_DEFAULT_URL, auth=None):
    logging.info('Creating MDB connection object: url=%s, has_auth=%s'
                 % (url, auth is not None))
    return _MDBClient(url, auth)
