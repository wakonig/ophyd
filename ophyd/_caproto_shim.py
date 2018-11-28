import threading

import atexit
import logging

from caproto.threading import pyepics_compat
from caproto.threading.pyepics_compat import PV as _PV, caput, caget  # noqa
from ._dispatch import _CallbackThread, EventDispatcher, wrap_callback


thread_class = threading.Thread
module_logger = logging.getLogger(__name__)
dispatcher = None
name = 'caproto'


class CaprotoCallbackThread(_CallbackThread):
    ...


class PV(_PV):
    def __init__(self, pvname, callback=None, form='time', verbose=False,
                 auto_monitor=None, count=None, connection_callback=None,
                 connection_timeout=None, access_callback=None,
                 context=None):
        connection_callback = wrap_callback(dispatcher, 'metadata',
                                            connection_callback)
        callback = wrap_callback(dispatcher, 'monitor', callback)
        access_callback = wrap_callback(dispatcher, 'metadata',
                                        access_callback)

        super().__init__(pvname, form=form, verbose=verbose,
                         auto_monitor=auto_monitor, count=count,
                         connection_timeout=connection_timeout,
                         connection_callback=connection_callback,
                         callback=callback, access_callback=access_callback,
                         context=context)

    def add_callback(self, callback=None, index=None, run_now=False,
                     with_ctrlvars=True, **kw):
        callback = wrap_callback(dispatcher, 'monitor', callback)
        return super().add_callback(callback=callback, index=index,
                                    run_now=run_now,
                                    with_ctrlvars=with_ctrlvars, **kw)

    def put(self, value, wait=False, timeout=30.0, use_complete=False,
            callback=None, callback_data=None):
        callback = wrap_callback(dispatcher, 'get_put', callback)
        return super().put(value, wait=wait, timeout=timeout,
                           use_complete=use_complete, callback=callback,
                           callback_data=callback_data)

    # TODO: caproto breaks API compatibility in wait_for_connection, raising TimeoutError

    def get_all_metadata(self):
        if self._args['timestamp'] is None:
            self.get_timevars()
        self.get_ctrlvars()
        md = self._args.copy()
        md.pop('value', None)
        return md

    def get_with_metadata(self, count=None, as_string=False, as_numpy=True,
                          timeout=None, with_ctrlvars=False, use_monitor=True):
        # TODO: this should be supported in caproto
        value = super().get(count=count, as_string=as_string,
                            as_numpy=as_numpy, timeout=timeout,
                            with_ctrlvars=with_ctrlvars,
                            use_monitor=use_monitor)
        if value is None:
            return value

        return {'value': value,
                'status': self._args['status'],
                'severity': self._args['severity'],
                'timestamp': self._args['timestamp'],
                }

    def clear_auto_monitor(self):
        # TODO move into caproto
        self.auto_monitor = False
        if self._auto_monitor_sub is not None:
            self._auto_monitor_sub.clear()
            self._auto_monitor_sub = None


def release_pvs(*pvs):
    for pv in pvs:
        pv.clear_callbacks()
        # pv.disconnect()


def get_pv(pvname, form='time', connect=False, context=None, timeout=5.0,
           connection_callback=None, access_callback=None, callback=None,
           **kwargs):
    """Get a PV from PV cache or create one if needed.

    Parameters
    ---------
    form : str, optional
        PV form: one of 'native' (default), 'time', 'ctrl'
    connect : bool, optional
        whether to wait for connection (default False)
    context : int, optional
        PV threading context (defaults to current context)
    timeout : float, optional
        connection timeout, in seconds (default 5.0)
    """
    if context is None:
        context = PV._default_context

    pv = PV(pvname, form=form, connection_callback=connection_callback,
            access_callback=access_callback, callback=callback,
            **kwargs)
    if connect:
        pv.wait_for_connection(timeout=timeout)
    return pv


def setup(logger):
    '''Setup ophyd for use

    Must be called once per session using ophyd
    '''
    # It's important to use the same context in the callback dispatcher
    # as the main thread, otherwise not-so-savvy users will be very
    # confused
    global dispatcher

    if dispatcher is not None:
        logger.debug('ophyd already setup')
        return

    pyepics_compat._get_pv = pyepics_compat.get_pv
    pyepics_compat.get_pv = get_pv

    def _cleanup():
        '''Clean up the ophyd session'''
        global dispatcher
        if dispatcher is None:
            return

        pyepics_compat.get_pv = pyepics_compat._get_pv

        logger.debug('Performing ophyd cleanup')
        if dispatcher.is_alive():
            logger.debug('Joining the dispatcher thread')
            dispatcher.stop()

        dispatcher = None

    logger.debug('Installing event dispatcher')
    context = PV._default_context.broadcaster
    dispatcher = EventDispatcher(thread_class=CaprotoCallbackThread,
                                 context=context, logger=logger)
    atexit.register(_cleanup)
    return dispatcher
