# Standard
import asyncio
import json
import re
import ssl
from datetime import datetime, timedelta
from typing import Any
from typing import Tuple
import os

import warnings
warnings.filterwarnings("ignore")


# Third
try:
    import aiohttp
    import pytz
except ImportError:
    print('Installing required packages...')
    os.system('pip install aiohttp pytz')
    import aiohttp
    import pytz


class AuthenticationError(Exception):
    """
    Raised whenever there's a problem while attempting to authenticate with the Riot server.
    """

    pass


def _extract_tokens(data: str) -> str:
    """Extract tokens from data"""

    pattern = re.compile(
        r'access_token=((?:[a-zA-Z]|\d|\.|-|_)*).*id_token=((?:[a-zA-Z]|\d|\.|-|_)*).*expires_in=(\d*)'
    )
    response = pattern.findall(data['response']['parameters']['uri'])[0]  # type: ignore
    return response


# https://developers.cloudflare.com/ssl/ssl-tls/cipher-suites/

FORCED_CIPHERS = [
    'ECDHE-ECDSA-AES256-GCM-SHA384',
    'ECDHE-ECDSA-AES128-GCM-SHA256',
    'ECDHE-ECDSA-CHACHA20-POLY1305',
    'ECDHE-RSA-AES128-GCM-SHA256',
    'ECDHE-RSA-CHACHA20-POLY1305',
    'ECDHE-RSA-AES128-SHA256',
    'ECDHE-RSA-AES128-SHA',
    'ECDHE-RSA-AES256-SHA',
    'ECDHE-ECDSA-AES128-SHA256',
    'ECDHE-ECDSA-AES128-SHA',
    'ECDHE-ECDSA-AES256-SHA',
    'ECDHE+AES128',
    'ECDHE+AES256',
    'ECDHE+3DES',
    'RSA+AES128',
    'RSA+AES256',
    'RSA+3DES',
]


class ClientSession(aiohttp.ClientSession):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.set_ciphers(':'.join(FORCED_CIPHERS))
        super().__init__(*args, **kwargs, cookie_jar=aiohttp.CookieJar(), connector=aiohttp.TCPConnector(ssl=ctx))


class Auth:
    RIOT_CLIENT_USER_AGENT = 'RiotClient/60.0.6.4770705.4749685 rso-auth (Windows;10;;Professional, x64)'

    def __init__(self) -> None:
        self._headers: dict = {
            'Content-Type': 'application/json',
            'User-Agent': Auth.RIOT_CLIENT_USER_AGENT,
            'Accept': 'application/json, text/plain, */*',
        }
        self.user_agent = Auth.RIOT_CLIENT_USER_AGENT

        self.locale_code = 'en-US'  # default language
        self.response = {}  # prepare response for local response

    def local_response(self) -> dict[str, Any]:
        """This function is used to check if the local response is enabled."""
        return {
            'INVALID_PASSWORD': 'Your username or password may be incorrect.',
            'RATELIMIT': 'Please wait a few minutes and try again.',
            'COOKIES_EXPIRED': 'Cookie has expired, please `/import` again.',
            'NO_NAME_TAG': 'This user has not created a name or tagline yet.',
            'REGION_NOT_FOUND': 'An unknown error occurred, please `/import` again.', 
            'LOGIN_COOKIE_FAILED': 'Login with cookie failed.', 
            'INPUT_2FA_CODE': 'Input 2FA code.', 
            '2FA_ENABLE': 'You have 2FA enabled.', 
            '2FA_TO_EMAIL': 'Riot sent a code to', 
            '2FA_INVALID_CODE': 'Code is invalid. Please use `/import` again.', 
            'TEMP_LOGIN_NOT_SUPPORT_2FA': '2FA is unsupported, please use `/cookies` to log in.'
        }

    async def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        """This function is used to authenticate the user."""

        # language
        local_response = self.local_response()

        session = ClientSession()

        data = {
            'client_id': 'play-valorant-web-prod',
            'nonce': '1',
            'redirect_uri': 'https://playvalorant.com/opt_in',
            'response_type': 'token id_token',
            'scope': 'account openid',
        }

        # headers = {'Content-Type': 'application/json', 'User-Agent': self.user_agent}

        r = await session.post('https://auth.riotgames.com/api/v1/authorization', json=data, headers=self._headers)

        # prepare cookies for auth request
        cookies = {'cookie': {}}
        for cookie in r.cookies.items():
            cookies['cookie'][cookie[0]] = str(cookie).split('=')[1].split(';')[0]

        data = {'type': 'auth', 'username': username, 'password': password, 'remember': True}

        async with session.put(
            'https://auth.riotgames.com/api/v1/authorization', json=data, headers=self._headers
        ) as r:
            data = await r.json()
            for cookie in r.cookies.items():
                cookies['cookie'][cookie[0]] = str(cookie).split('=')[1].split(';')[0]

        # print('Response Status:', r.status)
        await session.close()

        if data['type'] == 'response':
            expiry_token = datetime.now() + timedelta(hours=1)

            response = _extract_tokens(data)
            access_token = response[0]
            token_id = response[1]

            expiry_token = datetime.now() + timedelta(minutes=59)
            expiry_token = expiry_token.replace(tzinfo=pytz.utc)
            cookies['expiry_token'] = int(datetime.timestamp(expiry_token))  # type: ignore

            return {'auth': 'response', 'data': {'cookie': cookies, 'access_token': access_token, 'token_id': token_id}}

        elif data['type'] == 'multifactor':
            if r.status == 429:
                raise AuthenticationError(local_response.get('RATELIMIT', 'Please wait a few minutes and try again.'))

            label_modal = local_response.get('INPUT_2FA_CODE')
            WaitFor2FA = {'auth': '2fa', 'cookie': cookies, 'label': label_modal}

            if data['multifactor']['method'] == 'email':
                WaitFor2FA['message'] = (
                    f"{local_response.get('2FA_TO_EMAIL', 'Riot sent a code to')} {data['multifactor']['email']}"
                )
                return WaitFor2FA

            WaitFor2FA['message'] = local_response.get('2FA_ENABLE', 'You have 2FA enabled!')
            return WaitFor2FA

        raise AuthenticationError(local_response.get('INVALID_PASSWORD', 'Your username or password may be incorrect!'))

    async def get_entitlements_token(self, access_token: str) -> str:
        """This function is used to get the entitlements token."""

        # language
        local_response = self.local_response()

        session = ClientSession()

        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {access_token}'}

        async with session.post('https://entitlements.auth.riotgames.com/api/token/v1', headers=headers, json={}) as r:
            data = await r.json()

        await session.close()
        try:
            entitlements_token = data['entitlements_token']
        except KeyError as e:
            raise AuthenticationError(
                local_response.get('COOKIES_EXPIRED', 'Cookies is expired, plz /login again!')
            ) from e
        else:
            return entitlements_token

    async def get_userinfo(self, access_token: str) -> Tuple[str, str, str]:
        """This function is used to get the user info."""

        # language
        local_response = self.local_response()

        session = ClientSession()

        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {access_token}'}

        async with session.post('https://auth.riotgames.com/userinfo', headers=headers, json={}) as r:
            data = await r.json()

        await session.close()
        try:
            puuid = data['sub']
            name = data['acct']['game_name']
            tag = data['acct']['tag_line']
        except KeyError as e:
            raise AuthenticationError(
                local_response.get('NO_NAME_TAG', "This user hasn't created a name or tagline yet.")
            ) from e
        else:
            return puuid, name, tag

    async def get_region(self, access_token: str, token_id: str) -> str:
        """This function is used to get the region."""

        # language
        local_response = self.local_response()

        session = ClientSession()

        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {access_token}'}

        body = {'id_token': token_id}

        async with session.put(
            'https://riot-geo.pas.si.riotgames.com/pas/v1/product/valorant', headers=headers, json=body
        ) as r:
            data = await r.json()

        await session.close()
        try:
            region = data['affinities']['live']
        except KeyError as e:
            raise AuthenticationError(
                local_response.get('REGION_NOT_FOUND', 'An unknown error occurred, plz `/login` again')
            ) from e
        else:
            return region


    async def temp_auth(self, username: str, password: str) -> dict[str, Any] | None:
        authenticate = await self.authenticate(username, password)
        if authenticate['auth'] == 'response':  # type: ignore
            access_token = authenticate['data']['access_token']  # type: ignore
            token_id = authenticate['data']['token_id']  # type: ignore

            entitlements_token = await self.get_entitlements_token(access_token)
            puuid, name, tag = await self.get_userinfo(access_token)
            region = await self.get_region(access_token, token_id)
            player_name = f'{name}#{tag}' if tag is not None and tag is not None else 'no_username'

            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {access_token}',
                'X-Riot-Entitlements-JWT': entitlements_token,
            }
            user_data = {'puuid': puuid, 'region': region, 'headers': headers, 'player_name': player_name}
            return user_data

        raise AuthenticationError(self.local_response().get('TEMP_LOGIN_NOT_SUPPORT_2FA'))


async def main():
    username = input("Enter your username: ")
    password = input("Enter your password: ")

    auth = Auth()
    data = await auth.authenticate(username, password)
    auth_data = data['data']  # type: ignore
    cookie = auth_data['cookie']['cookie']
    access_token = auth_data['access_token']
    token_id = auth_data['token_id']

    try:
        entitlements_token = await auth.get_entitlements_token(access_token)
        puuid, name, tag = await auth.get_userinfo(access_token)
        region = await auth.get_region(access_token, token_id)
        player_name = f'{name}#{tag}' if tag is not None and tag is not None else 'no_username'

        expiry_token = datetime.timestamp(datetime.now(tz = pytz.utc) + timedelta(minutes=59))

        data = {
            'cookie': cookie,
            'access_token': access_token,
            'token_id': token_id,
            'emt': entitlements_token,
            'puuid': puuid,
            'username': player_name,
            'region': region,
            'expiry_token': expiry_token,
            'notify_mode': None,
            'DM_Message': True,
        }
        
        cp_data = json.dumps(data)
        os.system(f"echo {cp_data} | clip")
        print(cp_data)
        print("\n\nCopied to clipboard!")
    except Exception as e:
        print(e)

if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())
    print("\n\nPress Ctrl+C to exit.")
    loop.run_forever()