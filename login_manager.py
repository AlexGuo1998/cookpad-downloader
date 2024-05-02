import time
import json
import hmac
import hashlib
import typing

import requests
import urllib.parse
import cookpad_constants

_key1 = bytes(a ^ b for a, b in zip(
    b'\x1a\xa2\xfa<\x7fl\xaeq\xea\xd9\x15@u1\xd9V\x1aZ\xc6s\xa9\xf3\xa7\xccz\xb8\xa0\xbd\xd2&BN\x8f\xbd\xc2>\x87*M',
    b'[\xeb\x80],\x15\xec5\xbb\xba|#\x0ct\xb6\x03*?\x89=\x90\x96\xd5\xf4\x14\xc1\xf8\xfa\xa6\x103\x16\xc9\x88\x91y\xf7\x1c\x00')).decode('utf-8')
_key2 = bytes(a ^ b for a, b in zip(
    b'\xfc\x16+J\xdf \x9008\xb5\x9cq\xf7\x10\xb3\xca\x107\xb6\xf8',
    b'nJ\x1ae\x7f\x9f+\x7f\xe8N\x1c\x0e\x90y\xc0k\xce\xa9\xd6Y')).hex().upper()

AUTH_CENTER_ENDPOINT_FULL = 'https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyPassword?key=' + _key1
AUTH_REFRESH_URL = 'https://securetoken.googleapis.com/v1/token?key=' + _key1
AUTH_HEADERS = {
    # 'X-Client-Signature': sign,
    # 'X-CDID': self._get_cdid(),
    'X-Android-Package': 'com.cookpad.android.cookpad_tv',
    'X-Android-Cert': _key2,
    'User-Agent': 'Dalvik/2.1.0 (Linux; U; Android 7.1.1; ONEPLUS A3010 Build/NMF26F)',
    'Accept': None,
}

COOKPAD_TV_API_ENDPOINT = 'https://api.natslive.jp/'


class NotLoggedInError(ValueError):
    def __str__(self):
        return 'Not logged in!'


class LoginError(ValueError):
    def __init__(self, code, description, detail):
        self.code = code
        self.description = description
        self.detail = detail

    def __str__(self):
        return (
            f'Get token error (code {self.code}): {self.description}\n'
            f'Detail:\n'
            f'{self.detail}'
        )


class LoginManager:
    def __init__(self):
        self.cdid = None
        self.access_token = None
        self.refresh_token = None
        self.expire_ts = 0
        self.username = None
        self.password = None

        self.modified = False

    def login(self, username, password, save_password=False) -> None:
        self.modified = True
        self.username = username
        self.password = password if save_password else None
        data = {
            'clientType': "CLIENT_TYPE_ANDROID",
            'email': username,
            'password': password,
            'returnSecureToken': True
        }
        r = requests.post(AUTH_CENTER_ENDPOINT_FULL, json=data, headers=AUTH_HEADERS)
        self._decode_auth_result(r, is_refresh=False)

    def logout(self, cleanup=False):
        try:
            self.api_post('/api/v2/logout', fail_raise=True)
        except NotLoggedInError:
            pass
        self.modified = True
        self.access_token = None
        self.refresh_token = None
        self.expire_ts = 0
        if cleanup:
            self.username = None
            self.password = None

    def refresh(self, force=False) -> bool:
        # True: refresh OK
        # False: no need to refresh
        if self.refresh_token is None:
            raise NotLoggedInError()
        if not force and time.time() < self.expire_ts:
            return False  # no need to refresh
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': self.refresh_token,
        }
        r = requests.post(AUTH_REFRESH_URL, json=data, headers=AUTH_HEADERS)
        try:
            self._decode_auth_result(r, is_refresh=True)
        except LoginError:
            if self.username is None or self.password is None: raise
            # try password login
            self.login(self.username, self.password, save_password=True)
        return True

    def api_post(self, path, *args, fail_raise=False, **kwargs):
        if path.startswith('/'):
            path = path[1:]
        url = COOKPAD_TV_API_ENDPOINT + path
        headers = {
            'User-Agent': None,
            'X-COOKPAD-TV-CDID': self._get_cdid(),
            'X-Authorization': self.access_token,
        }
        r = requests.post(url, *args, **kwargs, headers=headers)
        try:
            self._api_auth_check(r)
            return r
        except NotLoggedInError:
            if fail_raise: raise
            self.refresh(force=True)
            return self.api_post(path, *args, **kwargs, fail_raise=True)

    def api_get(self, path, params=None, fail_raise=False):
        if path.startswith('/'):
            path = path[1:]
        url = COOKPAD_TV_API_ENDPOINT + path
        headers = {
            'User-Agent': None,
            'X-COOKPAD-TV-CDID': self._get_cdid(),
            'X-Authorization': self.access_token,
        }
        r = requests.get(url, params=params, headers=headers)
        try:
            self._api_auth_check(r)
            return r
        except NotLoggedInError:
            if fail_raise: raise
            self.refresh(force=True)
            return self.api_get(path, params, fail_raise=True)

    def api_graphql(self, operationName, query, variables=None):
        variables = variables or {}
        r = self.api_post('/api/graphql', json={
            "operationName": operationName,
            "query": query,
            "variables": variables
        })
        return r

    def check(self, access_web=True) -> typing.Tuple[bool, typing.Any]:
        result = None
        if self.access_token is None:
            return False, result
        if access_web:
            try:
                result = self.api_post('/api/v2/login')
            except NotLoggedInError:
                return False, result
        return True, result

    def dump_json(self) -> str:
        d = self.__dict__.copy()
        d.pop('modified', None)
        return json.dumps(d, ensure_ascii=False, separators=(',', ':'))

    def load_json(self, data):
        d = json.loads(data)
        self.__dict__.update((k, v) for k, v in d.items() if k in self.__dict__)
        self.modified = False

    @staticmethod
    def _api_auth_check(r):
        if r.status_code == 401:
            raise NotLoggedInError()

    def _get_cdid(self) -> str:
        if self.cdid is None:
            import uuid
            self.cdid = 'android:' + str(uuid.uuid4())
        return self.cdid

    def _send_auth_request(self, payload: dict):
        headers = {
            # 'X-Client-Signature': sign,
            # 'X-CDID': self._get_cdid(),
            'X-Android-Package': 'com.cookpad.android.cookpad_tv',
            'X-Android-Cert': '925C312FA0BFBB4FD0FB807F676973A1DE9E60A1',
            'User-Agent': 'Dalvik/2.1.0 (Linux; U; Android 7.1.1; ONEPLUS A3010 Build/NMF26F)',
            'Accept': None,
        }
        r = requests.post(AUTH_CENTER_ENDPOINT_FULL, json=payload, headers=headers)
        return r

    def _decode_auth_result(self, r, is_refresh):
        if r.status_code != 200:
            detail = r.content
            code = r.status_code
            description = None
            try:
                j = r.json()
                error = j.get('error')
                code = error.get('code', code)
                description = error.get('message')
            except Exception:
                pass
            raise LoginError(code, description, detail)
        j = r.json()
        if not is_refresh:
            # j = {
            #     'kind': 'identitytoolkit#VerifyPasswordResponse',
            #     'localId': '...',
            #     'email': '...@gmail.com',
            #     'displayName': '',
            #     'idToken': '...',
            #     'registered': True,
            #     'refreshToken': '...',
            #     'expiresIn': '3600',
            # }
            access_token = 'Bearer ' + j['idToken']
            refresh_token = j['refreshToken']
            expire_ts = int(time.time()) + int(j['expiresIn'])
        else:
            # j = {
            #     'access_token': '...',
            #     'expires_in': '3600',
            #     'id_token': '...',
            #     'project_id': '123',
            #     'refresh_token': '...',
            #     'token_type': 'Bearer',
            #     'user_id': '...'
            # }
            token_type = j['token_type']
            assert token_type == 'Bearer'
            access_token = 'Bearer ' + j['id_token']
            refresh_token = j['refresh_token']
            expire_ts = int(time.time()) + int(j['expires_in'])
        self.modified = True
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expire_ts = expire_ts


def get_manager():
    mgr = LoginManager()
    with open('login.json', 'r', encoding='utf-8') as f:
        mgr.load_json(f.read())
    return mgr


def save_manager(mgr):
    if mgr.modified:
        with open('login.json', 'w', encoding='utf-8') as f:
            f.write(mgr.dump_json())
        mgr.modified = False


def test_login():
    refreshed = False
    try:
        mgr = get_manager()
    except FileNotFoundError:
        print('create new')
        mgr = LoginManager()
        mgr.login(input('username: '), input('password: '))
        refreshed = True
        print('created')

    # refreshed = mgr.refresh(force=True)
    refreshed = mgr.refresh() or refreshed
    print(f'refreshed? {refreshed}')
    print(f'login check: {mgr.check()}')

    print(f"mgr.access_token = '{mgr.access_token}'")
    print(f"mgr.refresh_token = '{mgr.refresh_token}'")

    r = mgr.api_get('/api/v2/episode_details/12712', {
        'geometry[episode][width]': 640,  # image size (px)
        'geometry[teacher][width]': 640,
        'geometry[recipe][width]': 640,
        'fields': cookpad_constants.fields['EpisodeDetailEntity'],
    })
    print(r.json())

    if refreshed:
        save_manager(mgr)
        print('saved')
    else:
        print('not saved (unchanged)')


if __name__ == '__main__':
    test_login()
