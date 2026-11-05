"""Composition for a reviewed HTTPS host. No listener or session issuer is started.

Static shell is generic; all account evidence comes from the authenticated API.
Even if the underlying ControlAPI supports simulation commands, this monitor host
requires them disabled and rejects every non-GET request before dispatch.
"""
from pathlib import Path
from urllib.parse import urlsplit
from oneil_control_v2 import ControlAPI, Settings
from oneil_control_v2.bridge import CoreSimulationBackend
from .read_model import project_status, json_bytes

ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/index.html': ('index.html', 'text/html; charset=utf-8'),
          '/styles.css': ('styles.css', 'text/css; charset=utf-8'),
          '/app.mjs': ('app.mjs', 'text/javascript; charset=utf-8'),
          '/client.mjs': ('client.mjs', 'text/javascript; charset=utf-8'),
          '/viewmodel.mjs': ('viewmodel.mjs', 'text/javascript; charset=utf-8')}
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


class MonitorHost:
    def __init__(self, api):
        if type(api) is not ControlAPI or type(api.backend) is not CoreSimulationBackend:
            raise TypeError('REVIEWED_SIMULATION_CORE_REQUIRED')
        if api.projector is not project_status:
            raise TypeError('VERSIONED_PROJECTOR_REQUIRED')
        if api.settings.simulation_commands_enabled is not False:
            raise ValueError('MONITOR_COMMANDS_MUST_BE_DISABLED')
        self.api = api
        self.asset_root = Path(__file__).resolve().parent / 'web'

    def __call__(self, env, start_response):
        def reply(status, value):
            body = json_bytes({'error': value})
            start_response(status, [('Content-Type', 'application/json; charset=utf-8'),
                ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
                ('Content-Length', str(len(body))), ('Content-Security-Policy', "default-src 'none'"),
                ('Allow', 'GET')])
            return [body]
        if env.get('wsgi.url_scheme') != 'https' or env.get('HTTP_HOST') != urlsplit(self.api.settings.origin).netloc:
            return reply('400 Bad Request', 'TRUSTED_HTTPS_HOST_REQUIRED')
        if env.get('REQUEST_METHOD') != 'GET':
            return reply('405 Method Not Allowed', 'MONITOR_READ_ONLY')
        if env.get('QUERY_STRING', ''):
            return reply('400 Bad Request', 'QUERY_NOT_ALLOWED')
        path = env.get('PATH_INFO')
        if path == '/api/v2/status':
            return self.api(env, start_response)
        if path not in ASSETS:
            return reply('404 Not Found', 'NOT_FOUND')
        filename, content_type = ASSETS[path]
        body = (self.asset_root/filename).read_bytes()
        start_response('200 OK', [('Content-Type', content_type), ('Content-Length', str(len(body))),
            ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
            ('Content-Security-Policy', CSP), ('Referrer-Policy', 'no-referrer'),
            ('Strict-Transport-Security', 'max-age=31536000')])
        return [body]


def compose_monitor(*, state, store, origin, broker_max_age_seconds, clock):
    """Caller owns the isolated store/connection and trusted session journal.

    Does not open a connection, install credentials, activate an account, start a
    listener, or alter state. Must be composed separately in each serving thread.
    """
    api = ControlAPI(state=state, backend=CoreSimulationBackend(store), projector=project_status,
                     settings=Settings(origin, broker_max_age_seconds, False), clock=clock)
    return MonitorHost(api)
