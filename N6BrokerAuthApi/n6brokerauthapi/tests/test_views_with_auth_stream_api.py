# Copyright (c) 2013-2021 NASK. All rights reserved.

import itertools
import unittest

from unittest.mock import (
    MagicMock,
    call,
    patch,
)
from unittest_expander import (
    expand,
    foreach,
    param,
    paramseq,
)

from n6brokerauthapi.auth_stream_api import (
    StreamApiBrokerAuthManagerMaker,
    StreamApiBrokerAuthManager,
)
from n6brokerauthapi.views import (
    N6BrokerAuthResourceView,
    N6BrokerAuthVHostView,
    N6BrokerAuthUserView,
    N6BrokerAuthTopicView,
)
from n6lib.auth_db import models
from n6lib.class_helpers import attr_required
from n6lib.const import ADMINS_SYSTEM_GROUP_NAME
from n6lib.unit_test_helpers import (
    DBConnectionPatchMixin,
    RequestHelperMixin,
)


#
# Auxiliary constants
#

ADMINS_GROUP = ADMINS_SYSTEM_GROUP_NAME

AUTOGENERATED_QUEUE_PREFIX = 'stomp'

PUSH_EXCHANGE = '_push'
ORG1 = 'o1.example.com'
ORG2 = 'o2.example.com'

EXPLICITLY_ILLEGAL_USERNAMES = list(StreamApiBrokerAuthManager.EXPLICITLY_ILLEGAL_USERNAMES)
GUEST_USERNAME = 'guest'
assert GUEST_USERNAME in EXPLICITLY_ILLEGAL_USERNAMES

TEST_USER = 'test@example.org'          # its org is ORG1
ADMIN_USER = 'admin@example.net'        # its org is ORG2 + its system group is ADMINS_GROUP
REGULAR_USER = 'regular@example.info'   # its org is ORG2
UNKNOWN_USER = 'unknown@example.biz'    #  ^-- see below: `_MockerMixin._get_mocked_db_state()`

REGULAR_COMPONENT = 'regular-comp'
PRIVILEGED_COMPONENT = 'privileged-comp'
UNKNOWN_COMPONENT = 'unknown-comp'

KNOWN_USERS = [TEST_USER, ADMIN_USER, REGULAR_USER]
KNOWN_COMPONENTS = [REGULAR_COMPONENT, PRIVILEGED_COMPONENT]
KNOWN_USERS_AND_COMPONENTS = KNOWN_USERS + KNOWN_COMPONENTS

UNKNOWN_USERS = [UNKNOWN_USER, '']
UNKNOWN_COMPONENTS = [UNKNOWN_COMPONENT, '']
UNKNOWN_USERS_AND_COMPONENTS = UNKNOWN_USERS + UNKNOWN_COMPONENTS

ADMIN_USERS = [ADMIN_USER]

REGULAR_USERS = [TEST_USER, REGULAR_USER]
REGULAR_COMPONENTS = [REGULAR_COMPONENT]

EXCHANGE = 'exchange'
QUEUE = 'queue'
TOPIC = 'topic'

CONFIGURE = 'configure'
WRITE = 'write'
READ = 'read'


#
# Mixin classes and helper functions
#

class _MockerMixin(RequestHelperMixin, DBConnectionPatchMixin):

    # noinspection PyUnresolvedReferences
    def setUp(self):
        self.config = self.prepare_pyramid_unittesting()
        self.connector_mock = MagicMock()
        self._setup_auth_manager_maker()
        self._setup_db_mock()

    def _setup_auth_manager_maker(self):
        settings = {
            'stream_api_broker_auth.push_exchange_name': PUSH_EXCHANGE,
            'stream_api_broker_auth.privileged_component_logins': PRIVILEGED_COMPONENT,
            'stream_api_broker_auth.autogenerated_queue_prefix': AUTOGENERATED_QUEUE_PREFIX,
        }
        with patch('n6brokerauthapi.auth_base.SQLAuthDBConnector',
                   return_value=self.connector_mock):
            self.config.registry.auth_manager_maker = StreamApiBrokerAuthManagerMaker(settings)
        self.connector_mock.attach_mock(
            self.patch('n6brokerauthapi.auth_base.force_exit_on_any_remaining_entered_contexts'),
            'force_exit_on_any_remaining_entered_contexts_mock')

    def _setup_db_mock(self):
        db_state = self._get_mocked_db_state()
        self.make_patches(db_state, dict())

    def _get_mocked_db_state(self):
        # * users:
        test_user = models.User(login=TEST_USER)
        admin_user = models.User(login=ADMIN_USER)
        regular_user = models.User(login=REGULAR_USER)
        # * components:
        regular_comp = models.Component(login=REGULAR_COMPONENT)
        privileged_comp = models.Component(login=PRIVILEGED_COMPONENT)
        # (a special case: for any username that is present in the
        # `StreamApiBrokerAuthManager.EXPLICITLY_ILLEGAL_USERNAMES`
        # collection, in particular for the 'guest' username, access
        # will always be *denied* -- even if a matching record, such
        # as the following one, exists in the Auth DB)
        comp_whose_login_is_illegal_username = models.Component(login=GUEST_USERNAME)
        # * system groups:
        admins_group = models.SystemGroup(name=ADMINS_GROUP)
        # * organizations:
        org1 = models.Org(org_id=ORG1)
        org2 = models.Org(org_id=ORG2)
        # * relations:
        admins_group.users.append(admin_user)
        # noinspection PyUnresolvedReferences
        org1.users.append(test_user)
        # noinspection PyUnresolvedReferences
        org2.users.extend([admin_user, regular_user])
        # * whole DB state:
        db = {
            'user': [test_user, admin_user, regular_user],
            'component': [regular_comp, privileged_comp, comp_whose_login_is_illegal_username],
            'system_group': [admins_group],
            'org': [org1, org2],
        }
        return db

    def patch_db_connector(self, session_mock):
        """
        Patch the mocked database connector, so it returns
        a mocked session object, when it is used as a context
        manager.

        (This method implements the corresponding abstract method
        declared in `DBConnectionPatchMixin`.)
        """
        self.connector_mock.__enter__.return_value = session_mock

    def assertConnectorUsedOnlyAfterEnsuredClean(self):
        first_two_connector_uses = self.connector_mock.mock_calls[:2]
        if first_two_connector_uses:
            # noinspection PyUnresolvedReferences
            self.assertEqual(first_two_connector_uses, [
                call.force_exit_on_any_remaining_entered_contexts_mock(self.connector_mock),
                call.__enter__(),
            ])


# noinspection PyUnresolvedReferences
class _AssertResponseMixin:

    def assertAllow(self, resp):
        self.assertIn(resp.body, [b'allow', b'allow administrator'])
        self.assertEqual(resp.status_code, 200)

    def assertDeny(self, resp):
        self.assertEqual(resp.body, b'deny')
        self.assertEqual(resp.status_code, 200)

    def assertAdministratorTagPresent(self, resp):
        self.assertIn(b'administrator', resp.body.split())
        self.assertEqual(resp.status_code, 200)

    def assertNoAdministratorTag(self, resp):
        self.assertNotIn(b'administrator', resp.body.split())
        self.assertEqual(resp.status_code, 200)


class _N6BrokerViewTestingMixin(
        _MockerMixin,
        _AssertResponseMixin):

    # abstract stuff (must be specified in test classes):

    view_class = None

    @classmethod
    def basic_allow_params(cls):
        """
        Get some param dict for whom the view gives an "allow..."
        response. The dict should include only required params.

        This class method is used, in particular, to provide default
        param values for the `perform_request()` helper method.
        """
        raise NotImplementedError

    # private (class-local) helpers:

    @paramseq
    def __param_name_combinations(cls):
        required_param_names = sorted(cls.basic_allow_params())
        for i in range(len(required_param_names)):
            for some_param_names in itertools.combinations(required_param_names, i+1):
                assert set(some_param_names).issubset(required_param_names)
                yield list(some_param_names)

    @staticmethod
    def __adjust_params(params, kwargs):
        params.update(kwargs)
        for name, value in list(params.items()):
            if value is None:
                del params[name]

    # common helper:

    @attr_required('view_class')
    def perform_request(self, **kwargs):
        params = self.basic_allow_params()
        self.__adjust_params(params, kwargs)
        request = self.create_request(self.view_class, **params)
        resp = request.perform()
        self.assertConnectorUsedOnlyAfterEnsuredClean()
        return resp

    # common tests:

    def test_required_param_names(self):
        # noinspection PyUnresolvedReferences
        self.assertEqual(self.view_class.get_required_param_names(),
                         set(self.basic_allow_params()))

    def test_allow_despite_superfluous_params(self):
        resp = self.perform_request(whatever='spam')
        self.assertAllow(resp)

    def test_deny_for_multiple_values_of_any_request_param(self):
        resp = self.perform_request(whatever=['spam', 'ham'])
        self.assertDeny(resp)

    @foreach(__param_name_combinations)
    def test_deny_for_missing_request_params(self, some_param_names):
        resp = self.perform_request(**{name: None for name in some_param_names})
        self.assertDeny(resp)


def foreach_username(seq_of_usernames):
    seq_of_params = [param(username=username).label('u:' + username)
                     for username in seq_of_usernames]
    return foreach(seq_of_params)


#
# Actual tests
#

@expand
class TestUserView(_N6BrokerViewTestingMixin, unittest.TestCase):

    view_class = N6BrokerAuthUserView

    @classmethod
    def basic_allow_params(cls):
        # we omit 'password' as it is optional (and *not* supported by us yet)
        return dict(
            username=TEST_USER,
        )

    @foreach_username(EXPLICITLY_ILLEGAL_USERNAMES)
    def test_deny_for_explicitly_illegal_username(self, username):
        resp = self.perform_request(username=username)
        self.assertDeny(resp)

    @foreach_username(KNOWN_USERS_AND_COMPONENTS)
    def test_allow_for_any_known_user_or_component(self, username):
        resp = self.perform_request(username=username)
        self.assertAllow(resp)

    @foreach_username(UNKNOWN_USERS_AND_COMPONENTS)
    def test_deny_for_unknown_user_or_component(self, username):
        resp = self.perform_request(username=username)
        self.assertDeny(resp)

    @foreach_username(KNOWN_USERS_AND_COMPONENTS + UNKNOWN_USERS_AND_COMPONENTS)
    def test_deny_if_password_given(self, username):
        resp = self.perform_request(username=username,
                                    password='123')
        self.assertDeny(resp)

    @foreach_username(ADMIN_USERS)
    def test_allow_administrator_for_admin_user(self, username):
        resp = self.perform_request(username=username)
        self.assertAdministratorTagPresent(resp)
        self.assertAllow(resp)

    @foreach_username(REGULAR_USERS)
    def test_allow_without_administrator_for_known_non_admin_user(self, username):
        resp = self.perform_request(username=username)
        self.assertNoAdministratorTag(resp)
        self.assertAllow(resp)

    @foreach_username(KNOWN_COMPONENTS)
    def test_allow_without_administrator_for_any_known_component(self, username):
        resp = self.perform_request(username=username)
        self.assertNoAdministratorTag(resp)
        self.assertAllow(resp)


@expand
class TestVHostView(_N6BrokerViewTestingMixin, unittest.TestCase):

    view_class = N6BrokerAuthVHostView

    @classmethod
    def basic_allow_params(cls):
        return dict(
            username=TEST_USER,
            vhost='whatever',
            ip='1.2.3.4',
        )

    @foreach_username(EXPLICITLY_ILLEGAL_USERNAMES)
    def test_deny_for_explicitly_illegal_username(self, username):
        resp = self.perform_request(username=username)
        self.assertDeny(resp)

    @foreach_username(KNOWN_USERS_AND_COMPONENTS)
    def test_allow_for_any_known_user_or_component(self, username):
        resp = self.perform_request(username=username)
        self.assertAllow(resp)
        self.assertNoAdministratorTag(resp)

    @foreach_username(UNKNOWN_USERS_AND_COMPONENTS)
    def test_deny_for_unknown_user_or_component(self, username):
        resp = self.perform_request(username=username)
        self.assertDeny(resp)


@expand
class TestResourceView(_N6BrokerViewTestingMixin, unittest.TestCase):

    view_class = N6BrokerAuthResourceView

    @classmethod
    def basic_allow_params(cls):
        return dict(
            username=TEST_USER,
            vhost='whatever',
            resource=EXCHANGE,
            permission=READ,
            name=ORG1,
        )

    # private (class-local) helpers:

    @paramseq
    def __resource_types(cls):
        yield param(resource=EXCHANGE).label('ex')
        yield param(resource=QUEUE).label('qu')

    @paramseq
    def __illegal_resource_types(cls):
        yield param(resource=TOPIC)
        yield param(resource='whatever')
        yield param(resource='')

    @paramseq
    def __permission_levels(cls):
        yield param(permission=CONFIGURE).label('c')
        yield param(permission=WRITE).label('w')
        yield param(permission=READ).label('r')

    @paramseq
    def __illegal_permission_levels(cls):
        yield param(permission='whatever')
        yield param(permission='')

    @paramseq
    def __some_autogenerated_queue_names(cls):
        yield param(name=AUTOGENERATED_QUEUE_PREFIX + '.queue1')
        yield param(name=AUTOGENERATED_QUEUE_PREFIX + '-some_other_queue')
        yield param(name=AUTOGENERATED_QUEUE_PREFIX + '#$%#$afdiajsdfsadwe33')
        yield param(name=AUTOGENERATED_QUEUE_PREFIX)

    @paramseq
    def __some_not_autogenerated_queue_names(cls):
        yield param(name='stom.queue1')
        yield param(name='whatever')
        yield param(name='#$%#$afdiajsdfsadwe33')
        yield param(name='#')
        yield param(name='')

    @paramseq
    def __various_nonpush_exchange_names(cls):
        yield param(name=ORG1)
        yield param(name=ORG2)
        yield param(name='whatever')
        yield param(name='')

    __various_exchange_names = (__various_nonpush_exchange_names +
                                [param(name=PUSH_EXCHANGE)])

    __various_resource_names = (__some_autogenerated_queue_names +
                                __some_not_autogenerated_queue_names +
                                __various_exchange_names +
                                [param(name='foo.bar.spam')])

    @paramseq
    def __matching_knownuser_exchange_pairs(cls):
        # username=<login of User>, exchange=<org_id of User's Org>
        yield param(username=TEST_USER, name=ORG1)
        yield param(username=ADMIN_USER, name=ORG2)
        yield param(username=REGULAR_USER, name=ORG2)

    @paramseq
    def __not_matching_regularuser_exchange_pairs(cls):
        yield param(username=TEST_USER, name=ORG2)
        yield param(username=REGULAR_USER, name=ORG1)
        for username in REGULAR_USERS:
            for exchange in [PUSH_EXCHANGE, 'whatever', '']:
                yield param(username=username, name=exchange)

    # actual tests:

    # * cases with explicitly illegal usernames:

    @foreach_username(EXPLICITLY_ILLEGAL_USERNAMES)
    @foreach(__resource_types)
    @foreach(__permission_levels)
    @foreach(__various_resource_names)
    def test_deny_for_any_resource_and_permission_for_explicitly_illegal_username(
                                            self, username, resource, permission, name):
        resp = self.perform_request(
            username=username,
            resource=resource,
            permission=permission,
            name=name)
        self.assertDeny(resp)

    # * privileged access cases:

    @foreach(__resource_types)
    @foreach(__permission_levels)
    @foreach(__various_resource_names)
    def test_allow_for_any_resource_and_permission_for_admin_user(
                                            self, resource, permission, name):
        resp = self.perform_request(
            username=ADMIN_USER,
            resource=resource,
            permission=permission,
            name=name)
        self.assertAllow(resp)
        self.assertNoAdministratorTag(resp)

    @foreach(__resource_types)
    @foreach(__permission_levels)
    @foreach(__various_resource_names)
    def test_allow_for_any_resource_and_permission_for_privileged_component(
                                            self, resource, permission, name):
        resp = self.perform_request(
            username=PRIVILEGED_COMPONENT,
            resource=resource,
            permission=permission,
            name=name)
        self.assertAllow(resp)
        self.assertNoAdministratorTag(resp)

    # * 'exchange'-resource-related cases:

    @foreach_username(REGULAR_USERS + UNKNOWN_USERS)
    @foreach(__various_exchange_names)
    def test_deny_for_exchange_configure_by_any_non_admin_user(self, username, name):
        resp = self.perform_request(
            username=username,
            resource=EXCHANGE,
            permission=CONFIGURE,
            name=name)
        self.assertDeny(resp)

    @foreach_username(REGULAR_COMPONENTS + UNKNOWN_COMPONENTS)
    @foreach(__various_exchange_names)
    def test_deny_for_exchange_configure_by_any_unprivileged_component(self, username, name):
        resp = self.perform_request(
            username=username,
            resource=EXCHANGE,
            permission=CONFIGURE,
            name=name)
        self.assertDeny(resp)

    @foreach_username(REGULAR_USERS + UNKNOWN_USERS)
    @foreach(__various_nonpush_exchange_names)
    def test_deny_for_nonpush_exchange_write_by_any_non_admin_user(self, username, name):
        resp = self.perform_request(
            username=username,
            resource=EXCHANGE,
            permission=WRITE,
            name=name)
        self.assertDeny(resp)

    @foreach_username(REGULAR_COMPONENTS + UNKNOWN_COMPONENTS)
    @foreach(__various_nonpush_exchange_names)
    def test_deny_for_nonpush_exchange_write_by_any_unprivileged_component(self, username, name):
        resp = self.perform_request(
            username=username,
            resource=EXCHANGE,
            permission=WRITE,
            name=name)
        self.assertDeny(resp)

    @foreach_username(KNOWN_USERS_AND_COMPONENTS)
    def test_allow_for_push_exchange_write_by_any_known_user_or_component(self, username):
        resp = self.perform_request(
            username=username,
            resource=EXCHANGE,
            permission=WRITE,
            name=PUSH_EXCHANGE)
        self.assertAllow(resp)
        self.assertNoAdministratorTag(resp)

    @foreach_username(UNKNOWN_USERS_AND_COMPONENTS)
    def test_deny_for_push_exchange_write_by_unknown_user_or_component(self, username):
        resp = self.perform_request(
            username=username,
            resource=EXCHANGE,
            permission=WRITE,
            name=PUSH_EXCHANGE)
        self.assertDeny(resp)

    @foreach(__matching_knownuser_exchange_pairs)
    def test_allow_for_exchange_read_by_known_user_whose_org_matches_exchange(
                                                        self, username, name):
        resp = self.perform_request(
            username=username,
            resource=EXCHANGE,
            permission=READ,
            name=name)
        self.assertAllow(resp)
        self.assertNoAdministratorTag(resp)

    @foreach(__not_matching_regularuser_exchange_pairs)
    def test_deny_for_exchange_read_by_regular_user_whose_org_does_not_match_exchange(
                                                        self, username, name):
        resp = self.perform_request(
            username=username,
            resource=EXCHANGE,
            permission=READ,
            name=name)
        self.assertDeny(resp)

    @foreach_username(UNKNOWN_USERS)
    @foreach(__various_exchange_names)
    def test_deny_for_exchange_read_by_unknown_user(self, username, name):
        resp = self.perform_request(
            username=username,
            resource=EXCHANGE,
            permission=READ,
            name=name)
        self.assertDeny(resp)

    # * 'queue'-resource-related cases:

    @foreach_username(REGULAR_COMPONENTS + UNKNOWN_COMPONENTS)
    @foreach(__various_exchange_names)
    def test_deny_for_exchange_read_by_any_unprivileged_component(self, username, name):
        resp = self.perform_request(
            username=username,
            resource=EXCHANGE,
            permission=READ,
            name=name)
        self.assertDeny(resp)

    @foreach_username(KNOWN_USERS_AND_COMPONENTS)
    @foreach(__permission_levels)
    @foreach(__some_autogenerated_queue_names)
    def test_allow_for_any_permission_for_autogenerated_queue_for_any_known_user_or_component(
                                            self, username, permission, name):
        resp = self.perform_request(
            username=username,
            resource=QUEUE,
            permission=permission,
            name=name)
        self.assertAllow(resp)
        self.assertNoAdministratorTag(resp)

    @foreach_username(UNKNOWN_USERS_AND_COMPONENTS)
    @foreach(__permission_levels)
    @foreach(__some_autogenerated_queue_names)
    def test_deny_for_any_permission_for_autogenerated_queue_for_any_unknown_user_or_component(
                                            self, username, permission, name):
        resp = self.perform_request(
            username=username,
            resource=QUEUE,
            permission=permission,
            name=name)
        self.assertDeny(resp)

    @foreach_username(REGULAR_USERS + UNKNOWN_USERS)
    @foreach(__permission_levels)
    @foreach(__some_not_autogenerated_queue_names)
    def test_deny_for_any_permission_for_not_autogenerated_queue_for_any_non_admin_user(
                                            self, username, permission, name):
        resp = self.perform_request(
            username=username,
            resource=QUEUE,
            permission=permission,
            name=name)
        self.assertDeny(resp)

    @foreach_username(REGULAR_COMPONENTS + UNKNOWN_COMPONENTS)
    @foreach(__permission_levels)
    @foreach(__some_not_autogenerated_queue_names)
    def test_deny_for_any_permission_for_not_autogenerated_queue_for_any_unprivileged_component(
                                            self, username, permission, name):
        resp = self.perform_request(
            username=username,
            resource=QUEUE,
            permission=permission,
            name=name)
        self.assertDeny(resp)

    # * illegal resource/permission cases:

    @foreach_username(KNOWN_USERS_AND_COMPONENTS + UNKNOWN_USERS_AND_COMPONENTS)
    @foreach(__illegal_resource_types)
    @foreach(__permission_levels)
    @foreach(__various_resource_names)
    def test_deny_for_illegal_resource_type(self, username, resource, permission, name):
        resp = self.perform_request(
            username=username,
            resource=resource,
            permission=permission,
            name=name)
        self.assertDeny(resp)

    @foreach_username(KNOWN_USERS_AND_COMPONENTS + UNKNOWN_USERS_AND_COMPONENTS)
    @foreach(__resource_types)
    @foreach(__illegal_permission_levels)
    @foreach(__various_resource_names)
    def test_deny_for_illegal_permission_level(self, username, resource, permission, name):
        resp = self.perform_request(
            username=username,
            resource=resource,
            permission=permission,
            name=name)
        self.assertDeny(resp)


@expand
class TestTopicView(_N6BrokerViewTestingMixin, unittest.TestCase):

    view_class = N6BrokerAuthTopicView

    @classmethod
    def basic_allow_params(cls):
        return dict(
            username=TEST_USER,
            vhost='whatever',
            resource=TOPIC,
            permission=READ,
            name='whatever',
            routing_key='whatever',
        )

    # private (class-local) helpers:

    @paramseq
    def __permission_levels(cls):
        yield param(permission=WRITE).label('w')
        yield param(permission=READ).label('r')

    @paramseq
    def __illegal_permission_levels(cls):
        yield param(permission=CONFIGURE)
        yield param(permission='whatever')

    @paramseq
    def __illegal_resource_types(cls):
        yield param(resource=EXCHANGE)
        yield param(resource=QUEUE)
        yield param(resource='whatever')

    @paramseq
    def __various_exchange_names(cls):
        yield param(name=ORG1)
        yield param(name=ORG2)
        yield param(name=PUSH_EXCHANGE)
        yield param(name='whatever')

    # actual tests:

    @foreach_username(EXPLICITLY_ILLEGAL_USERNAMES)
    @foreach(__permission_levels)
    @foreach(__various_exchange_names)
    def test_deny_for_explicitly_illegal_username(self, username, permission, name):
        resp = self.perform_request(
            username=username,
            permission=permission,
            name=name)
        self.assertDeny(resp)

    @foreach_username(KNOWN_USERS_AND_COMPONENTS)
    @foreach(__permission_levels)
    @foreach(__various_exchange_names)
    def test_allow_for_any_known_user_or_component(self, username, permission, name):
        resp = self.perform_request(
            username=username,
            permission=permission,
            name=name)
        self.assertAllow(resp)
        self.assertNoAdministratorTag(resp)

    @foreach_username(UNKNOWN_USERS_AND_COMPONENTS)
    @foreach(__permission_levels)
    @foreach(__various_exchange_names)
    def test_deny_for_unknown_user_or_component(self, username, permission, name):
        resp = self.perform_request(
            username=username,
            permission=permission,
            name=name)
        self.assertDeny(resp)

    @foreach_username(KNOWN_USERS_AND_COMPONENTS + UNKNOWN_USERS_AND_COMPONENTS)
    @foreach(__illegal_resource_types)
    @foreach(__permission_levels)
    @foreach(__various_exchange_names)
    def test_deny_for_illegal_resource_type(self, username, resource, permission, name):
        resp = self.perform_request(
            username=username,
            resource=resource,
            permission=permission,
            name=name)
        self.assertDeny(resp)

    @foreach_username(KNOWN_USERS_AND_COMPONENTS + UNKNOWN_USERS_AND_COMPONENTS)
    @foreach(__illegal_permission_levels)
    @foreach(__various_exchange_names)
    def test_deny_for_illegal_permission_level(self, username, permission, name):
        resp = self.perform_request(
            username=username,
            permission=permission,
            name=name)
        self.assertDeny(resp)


if __name__ == '__main__':
    unittest.main()
