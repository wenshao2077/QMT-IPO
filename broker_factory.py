"""Explicit transport selection; never switch routes after an uncertain request."""
from broker import Broker as XtBroker, ReadOnlyBroker as XtReadOnlyBroker


def Broker(config):
    if config.get('transport', 'xtquant') == 'xtquant':
        return XtBroker(config)
    if config.get('transport') == 'native':
        from native_broker import NativeBroker
        return NativeBroker(config)
    raise ValueError('unknown_broker_transport')


def ReadOnlyBroker(config):
    if config.get('transport', 'xtquant') == 'xtquant':
        return XtReadOnlyBroker(config)
    if config.get('transport') == 'native':
        from native_broker import NativeBroker
        return NativeBroker(config, read_only=True)
    raise ValueError('unknown_broker_transport')
