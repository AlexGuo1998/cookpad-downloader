import time
import json
import hmac
import hashlib
import typing

import requests
import urllib.parse
import cookpad_constants

AUTH_CENTER_ENDPOINT_FULL = 'https://auth.cookpad.com' + '/oauth/token'
# AUTH_CENTER_ENDPOINT_FULL = 'https://httpbin.org/post'
AUTH_CENTER_CLIENT_ID = bytes(a ^ b for a, b in zip(
    b'\xb44\xec\xa5\x03\x7f\xcao\xb6X\xeb\xf4\xbb\xe4\xe5\x08!\xeb\x0eG\xf5\\\x95\xd1Q&\x01A\x96~v\x14',
    b'K\\\x0c\xd6f\xb9\x9cEjj~\xba:\xb2-q\xc2\xf3\xff\xc5\x9c\x05\x8e\x1e\xde\xf0#\\\xfb\x89\x03\xf3')).hex()
AUTH_CENTER_SECRET_KEY = bytes(a ^ b for a, b in zip(
    b'J\xda\xb1\x8d\xf1\xfe\xba\x17\xc1\xde\x8cE\xaeL\x1f\xef\x046]\xdb',
    b'V\x963A\x98\rlIw5L_\x86ZK\x00z`\x16\x8d')).hex().encode('ascii')
COOKPAD_TV_API_ENDPOINT = 'https://api.cookpad.tv/'

'''
method
###### Class p310g.p344c.p351b.AuthCenterClient (g.c.b.a)
.class public final Lg/c/b/a;
.super Ljava/lang/Object;
.source "AuthCenterClient.kt"

key
###### Class com.cookpad.android.cookpad_tv.BuildConfig (com.cookpad.android.cookpad_tv.b)
.class public final Lcom/cookpad/android/cookpad_tv/b;
.super Ljava/lang/Object;
.source "BuildConfig.java"

api
com.cookpad.android.cookpad_tv.core.data.api.CookpadTVService
'''


class NotLoggedInError(ValueError):
    def __str__(self):
        return 'Not logged in!'


class LoginError(ValueError):
    def __init__(self, code, error, description):
        self.code = code
        self.error = error
        self.description = description

    def __str__(self):
        return f'Get token error {self.error} (HTTP code {self.code}): {self.description}'
        # return f'Invalid box {self.box} when splitting'


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
            'grant_type': 'signed_password',
            'username': username,
            'password': password,
            'client_id': AUTH_CENTER_CLIENT_ID,
            'scope': 'bundle.cookpad',
        }
        r = self._send_auth_request(data)
        self._decode_auth_result(r)

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
            'grant_type': 'signed_refresh_token',
            'refresh_token': self.refresh_token,
            'client_id': AUTH_CENTER_CLIENT_ID,
            'scope': 'bundle.cookpad',
        }
        r = self._send_auth_request(data)
        try:
            self._decode_auth_result(r)
        except LoginError:
            if self.username is None or self.password is None: raise
            # try password login
            self.login(self.username, self.password, save_password=True)
        return True

    def api_post(self, path, params=None, payload=None, fail_raise=False):
        if path.startswith('/'):
            path = path[1:]
        url = COOKPAD_TV_API_ENDPOINT + path
        headers = {
            # 'User-Agent': 'com.cookpad.android.cookpad_tv/200701; Android/23; MuMu; ; release-023b44951',
            'User-Agent': None,
            # 'X-COOKPAD-TV-APP-VERSION': '200701',
            # 'X-COOKPAD-TV-OS-VERSION': '23',
            'X-COOKPAD-TV-CDID': self._get_cdid(),
            # 'X-Model': 'MuMu',
            # 'X-Product': 'cancro',
            # 'X-Brand': 'Android',
            'Authorization': self.access_token,
        }
        r = requests.post(url, params=params, data=payload, headers=headers)
        try:
            self._api_auth_check(r)
            return r
        except NotLoggedInError:
            if fail_raise: raise
            self.refresh(force=True)
            return self.api_post(path, params, payload, fail_raise=True)

    def api_get(self, path, params=None, fail_raise=False):
        if path.startswith('/'):
            path = path[1:]
        url = COOKPAD_TV_API_ENDPOINT + path
        headers = {
            # 'User-Agent': 'com.cookpad.android.cookpad_tv/200701; Android/23; MuMu; ; release-023b44951',
            'User-Agent': None,
            # 'X-COOKPAD-TV-APP-VERSION': '200701',
            # 'X-COOKPAD-TV-OS-VERSION': '23',
            'X-COOKPAD-TV-CDID': self._get_cdid(),
            # 'X-Model': 'MuMu',
            # 'X-Product': 'cancro',
            # 'X-Brand': 'Android',
            'Authorization': self.access_token,
        }
        r = requests.get(url, params=params, headers=headers)
        try:
            self._api_auth_check(r)
            return r
        except NotLoggedInError:
            if fail_raise: raise
            self.refresh(force=True)
            return self.api_get(path, params, fail_raise=True)

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
        if 'nonce' not in payload:
            payload['nonce'] = str(int(time.time() * 1000))

        data = []
        for k, v in payload.items():
            k = urllib.parse.quote(k, encoding='utf-8')
            v = urllib.parse.quote(v, encoding='utf-8')
            data.append(f'{k}={v}')
        data_str = '&'.join(data).encode('utf-8')

        # HMAC-SHA256
        sign = hmac.new(AUTH_CENTER_SECRET_KEY, data_str, hashlib.sha256).hexdigest()

        headers = {
            'X-Client-Signature': sign,
            'X-CDID': self._get_cdid(),
            'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
            'User-Agent': 'okhttp/4.8.1',
            'Accept': None,
        }
        r = requests.post(AUTH_CENTER_ENDPOINT_FULL, data_str, headers=headers)
        return r

    def _decode_auth_result(self, r):
        if r.status_code != 200:
            error = None
            description = None
            try:
                j = r.json()
                error = j.get('error')
                description = j.get('error_description')
            except Exception:
                pass
            raise LoginError(r.status_code, error, description)
        j = r.json()
        # j = {
        #     'access_token': '...',
        #     'expires_in': '3600',
        #     'refresh_token': '...',
        #     'resource_owner_id': 39907799,
        #     'scope': 'bundle.cookpad',
        #     'token_type': 'bearer'
        # }
        token_type = j['token_type']
        assert token_type == 'bearer'
        access_token = 'Bearer ' + j['access_token']
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
