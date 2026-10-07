"""Retired operator pairing. Personal-server agent configuration is separate."""
from .retirement import RETIRED_MESSAGE


def pair_server_url(value):
    raise ValueError(RETIRED_MESSAGE)


def server_url(value):
    raise ValueError(RETIRED_MESSAGE)


def connect_ticket(value, ticket):
    raise ValueError(RETIRED_MESSAGE)


def connect(value, username, password):
    raise ValueError(RETIRED_MESSAGE)


def register_pairing(app, runtime, guard, csrf):
    from flask import jsonify, redirect, request
    from .execution import mode
    if mode() != 'local':
        return

    @app.get('/connect')
    def connect_page():
        return redirect(request.script_root + ('/' if runtime.configured.is_set() else '/setup'))

    @app.post('/api/instance/pair')
    def local_pair():
        try:
            guard(write=True)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(error=RETIRED_MESSAGE), 410
