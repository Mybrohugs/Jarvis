"""Sony power/app control through the existing local HA credential store.

Video transport remains tv_media / the paired YouTube channel. Called only
inside the router's confirmed tv-play action, never directly by the gateway.
"""
from ..home_assistant import SonyConnector


def configured_connector():
    from .station import keys
    store = keys()
    if not store.configured():
        return None
    return SonyConnector(store.load(), allow_control=True)


class HomeAssistantTv:
    def __init__(self, factory=configured_connector):
        self.factory = factory

    def wake(self):
        client = self.factory()
        if client is None:
            return None
        state = client.status().state
        if state in ('unknown', 'unavailable'):
            return None
        if state == 'off':
            client.turn_on()
            return True
        return False

    def open_youtube(self):
        client = self.factory()
        if client is not None:
            client.open_youtube()
