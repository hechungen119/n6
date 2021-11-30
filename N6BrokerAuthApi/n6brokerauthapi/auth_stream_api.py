# Copyright (c) 2013-2021 NASK. All rights reserved.

import re

from sqlalchemy.orm.exc import NoResultFound

from n6brokerauthapi.auth_base import (
    BaseBrokerAuthManagerMaker,
    BaseBrokerAuthManager,
)
from n6lib.auth_db import models
from n6lib.common_helpers import ascii_str
from n6lib.config import ConfigMixin
from n6lib.context_helpers import ThreadLocalNamespace
from n6lib.log_helpers import get_logger


LOGGER = get_logger(__name__)


class StreamApiBrokerAuthManagerMaker(ConfigMixin, BaseBrokerAuthManagerMaker):

    config_spec = """
        [stream_api_broker_auth]
        push_exchange_name = _push :: str
        privileged_component_logins = rabbit-inner :: list_of_str
        autogenerated_queue_prefix = stomp :: str
    """

    def __init__(self, settings):
        super().__init__(settings=settings)
        self._config = self.get_config_section(settings)
        self._thread_local = ThreadLocalNamespace(attr_factories={
            'autogenerated_queue_matcher': self._make_autogenerated_queue_matcher,
        })

    def _make_autogenerated_queue_matcher(self):
        prefix = self._config['autogenerated_queue_prefix']
        if prefix:
            re_escaped_prefix = re.escape(prefix)
            regex = re.compile(r'\A({}.*)\Z'.format(re_escaped_prefix))
            return regex.search
        return lambda _: None

    def get_manager_factory(self, params):
        return StreamApiBrokerAuthManager

    def get_manager_factory_kwargs(self, params):
        base = super().get_manager_factory_kwargs(params)
        return dict(base,
                    push_exchange_name=self._config['push_exchange_name'] or None,
                    privileged_component_logins=self._config['privileged_component_logins'],
                    autogenerated_queue_matcher=self._thread_local.autogenerated_queue_matcher)


class StreamApiBrokerAuthManager(BaseBrokerAuthManager):

    def __init__(self,
                 push_exchange_name,
                 privileged_component_logins,
                 autogenerated_queue_matcher,
                 **kwargs):
        self._push_exchange_name = push_exchange_name
        self._privileged_component_logins = privileged_component_logins
        self._autogenerated_queue_matcher = autogenerated_queue_matcher
        super().__init__(**kwargs)


    EXPLICITLY_ILLEGAL_USERNAMES = ('', 'guest')

    def should_try_to_verify_client(self):
        if self.broker_username in self.EXPLICITLY_ILLEGAL_USERNAMES:
            LOGGER.error(
                "The '%a' username is explicitly considered illegal!",
                ascii_str(self.broker_username))
            return False
        if self.password is not None:
            LOGGER.error(
                "Authentication by password is not supported - cannot authenticate '%a'!",
                ascii_str(self.broker_username))
            return False
        return super().should_try_to_verify_client()

    def verify_and_get_user_obj(self):
        user_obj = self._from_db(models.User, 'login', self.broker_username)
        if user_obj is not None:
            org_obj = user_obj.org
            assert org_obj is not None   # (guaranteed because `User.org_id` is not nullable)
        return user_obj

    def verify_and_get_component_obj(self):
        return self._from_db(models.Component, 'login', self.broker_username)

    def _from_db(self, model, col_name, val):
        try:
            return self.db_session.query(model).filter(getattr(model, col_name) == val).one()
        except NoResultFound:
            return None


    def apply_privileged_access_rules(self):
        # the conditions are met if the client is verified *and*:
        # * is a user belonging to the "admins" system group in the
        #   Auth DB,
        # * or is one of the privileged components specified in the
        #   configuration (see option `privileged_component_logins`).
        if (self.client_is_admin_user
            or (self.client_type == 'component'
                and self.client_obj.login in self._privileged_component_logins)):
            assert self.client_verified   # (must be true when the above `if`'s condition is true)
            return True
        return False

    def apply_vhost_rules(self):
        # maybe TODO later: wouldn't it be nice to be able to specify allowed vhosts in config?
        return self.client_verified

    def apply_exchange_rules(self):
        # refuse access if client is not verified; otherwise:
        # never give the "configure" permission; "write" only
        # to a certain common exchange; "read" only from an
        # exchange whose name is the user's organisation ID
        if not self.client_verified:
            return False
        exchange_name = self.res_name
        assert exchange_name is not None   # (guaranteed thanks to view's `validate_params()`...)
        if self.permission_level == 'configure':
            return False
        if (self.permission_level == 'write'
                and self._push_exchange_name is not None
                and exchange_name == self._push_exchange_name):
            return True
        if (self.permission_level == 'read'
                and self.client_type == 'user'
                and self.client_obj.org is not None
                and self.client_obj.org.org_id == exchange_name):
            return True
        return False

    def apply_queue_rules(self):
        # refuse access if client is not verified; otherwise:
        # grant all permissions for autogenerated queues
        # (these are user-created queues, whose names are
        # auto-generated by the broker; unknown to other users)
        if not self.client_verified:
            return False
        queue_name = self.res_name
        assert queue_name is not None   # (guaranteed thanks to view's `validate_params()`...)
        return (self._autogenerated_queue_matcher(queue_name) is not None)

    def apply_topic_rules(self):
        # note that *topic_path* is queried only if *resource_path*
        # has given an "allow..." response
        return self.client_verified
