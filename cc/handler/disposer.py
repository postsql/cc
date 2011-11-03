from cc.handler import CCHandler

__all__ = ['Disposer']

CC_HANDLER = 'Disposer'

class Disposer (CCHandler):
    """ Discards any message received """

    CC_ROLES = ['local', 'remote']

    def handle_msg (self, cmsg):
        """ Got message from client -- discard it :-) """
        return
