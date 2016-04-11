# coding=utf-8
"""Test the CRUD API endpoints `OSTree`_ `repositories`_.

This module assumes that the tests in
:mod:`pulp_smash.tests.platform.api_v2.test_repository` hold true. The
following trees of assumptions are explored in this module::

    It is possible to create an OSTree repo with feed (CreateTestCase).
    It is possible to create a repository without a feed (CreateTestCase).
      It is possible to create distributors for a repo
        It is not possible to create distributors to have conflicting paths
        It is not possible to update distrubutors to have conflicting paths

.. _OSTree:
    http://pulp-ostree.readthedocs.org/en/latest/
.. _repositories:
   http://pulp.readthedocs.org/en/latest/dev-guide/integration/rest-api/repo/cud.html
"""
from __future__ import unicode_literals

from pulp_smash import api, selectors, utils
from pulp_smash.compat import urljoin
from pulp_smash.constants import REPOSITORY_PATH
from pulp_smash.tests.ostree.utils import gen_repo


def setUpModule():  # pylint:disable=invalid-name
    """Skip tests if the OSTree plugin is not installed."""
    utils.skip_if_type_is_unsupported('ostree')


def _gen_distributor(relative_path):
    """Return a dict for use in creating a distributor."""
    return {
        'config': {},
        'distributor_config': {'relative_path': relative_path},
        'distributor_type_id': 'ostree_web_distributor',
    }


def _gen_rel_path(segments=2):
    """Return a semi-random relative path."""
    return '/'.join((utils.uuid4() for _ in range(segments)))


class CreateTestCase(utils.BaseAPITestCase):
    """Create two OSTree repositories, with and without a feed."""

    @classmethod
    def setUpClass(cls):
        """Create two repositories."""
        super(CreateTestCase, cls).setUpClass()
        client = api.Client(cls.cfg, api.json_handler)
        cls.bodies = tuple((gen_repo() for _ in range(2)))
        cls.bodies[1]['importer_config'] = {'feed': utils.uuid4()}
        cls.repos = [client.post(REPOSITORY_PATH, body) for body in cls.bodies]
        cls.importers_iter = [
            client.get(urljoin(repo['_href'], 'importers/'))
            for repo in cls.repos
        ]
        for repo in cls.repos:
            cls.resources.add(repo['_href'])  # mark for deletion

    def test_id_notes(self):
        """Validate the ``id`` and ``notes`` attributes for each repository."""
        for body, repo in zip(self.bodies, self.repos):  # for input, output:
            for key in {'id', 'notes'}:
                with self.subTest(body=body):
                    self.assertIn(key, repo)
                    self.assertEqual(repo[key], body[key])

    def test_number_importers(self):
        """Assert each repository has one importer."""
        for body, importers in zip(self.bodies, self.importers_iter):
            with self.subTest(body=body):
                self.assertEqual(len(importers), 1, importers)

    def test_importer_type_id(self):
        """Validate the ``importer_type_id`` attribute of each importer."""
        key = 'importer_type_id'
        for body, importers in zip(self.bodies, self.importers_iter):
            with self.subTest(body=body):
                self.assertIn(key, importers[0])
                self.assertEqual(importers[0][key], body[key])

    def test_importer_config(self):
        """Validate the ``config`` attribute of each importer."""
        key = 'config'
        for body, importers in zip(self.bodies, self.importers_iter):
            with self.subTest(body=body):
                self.assertIn(key, importers[0])
                self.assertEqual(importers[0][key], body['importer_' + key])


class CreateDistributorsTestCase(utils.BaseAPITestCase):
    """Show Pulp can create OSTree distributors and prevent path conflicts.

    It is valid for the following distributor relative paths to coexist:

    * ``foo/bar``
    * ``foo/biz``
    * ``foo/baz/abc``

    But given the above, the following distributor relative paths conflict:

    * ``foo/bar``
    * ``foo/bar/biz``
    * ``/foo/bar``
    """

    @classmethod
    def setUpClass(cls):
        """Create distributors with legal and illegal relative paths."""
        super(CreateDistributorsTestCase, cls).setUpClass()
        cls.responses = []

        relative_paths = [_gen_rel_path(), _gen_rel_path(), _gen_rel_path(3)]
        relative_paths.append(relative_paths[0])
        relative_paths.append(relative_paths[0] + '/' + utils.uuid4())
        relative_paths.append('/' + relative_paths[0])

        # Create two repositories
        client = api.Client(cls.cfg, api.json_handler)
        repos = [client.post(REPOSITORY_PATH, gen_repo()) for _ in range(2)]
        for repo in repos:
            cls.resources.add(repo['_href'])  # mark for deletion

        # Create a distributor for the first repository
        client.response_handler = api.echo_handler
        path = urljoin(repos[0]['_href'], 'distributors/')
        body = _gen_distributor(relative_paths[0])
        cls.responses.append(client.post(path, body))

        # Create distributors for the second repository
        path = urljoin(repos[1]['_href'], 'distributors/')
        for relative_path in relative_paths[1:]:
            body = _gen_distributor(relative_path)
            cls.responses.append(client.post(path, body))

    def test_successes(self):
        """Verify Pulp creates distributors when given good relative paths."""
        for i, response in enumerate(self.responses[:3]):
            with self.subTest(i=i):
                self.assertEqual(response.status_code, 201)

    def test_failures(self):
        """Verify Pulp doesn't create distributors when given bad rel paths."""
        if selectors.bug_is_untestable(1106, self.cfg.version):
            self.skipTest('https://pulp.plan.io/issues/1106')
        for i, response in enumerate(self.responses[3:]):
            with self.subTest(i=i):
                self.assertEqual(response.status_code, 400)


class UpdateDistributorsTestCase(utils.BaseAPITestCase):
    """Test the update of ostree distributors."""

    @classmethod
    def setUpClass(cls):
        """Create distributors and update with conflicting relative_paths."""
        super(UpdateDistributorsTestCase, cls).setUpClass()

        # Create two repository + distributor pairs.
        client = api.Client(cls.cfg, api.json_handler)
        distributors = []
        for _ in range(2):
            repo = client.post(REPOSITORY_PATH, gen_repo())
            cls.resources.add(repo['_href'])  # mark for deletion
            distributors.append(client.post(
                urljoin(repo['_href'], 'distributors/'),
                _gen_distributor(_gen_rel_path()),
            ))

        # Update the second distributor several times. After each update, we
        # read the distributor. This extra read is necessary b/c the initial
        # response is a call report.
        cls.written_paths = (
            _gen_rel_path(),  # successes
            _gen_rel_path(3),
            distributors[0]['config']['relative_path'],  # failures
            distributors[0]['config']['relative_path'] + '/' + utils.uuid4(),
            '/' + distributors[0]['config']['relative_path'],
        )
        cls.responses = []
        cls.read_paths = []
        for relative_path in cls.written_paths:
            client.response_handler = api.echo_handler
            cls.responses.append(client.put(
                distributors[1]['_href'],
                {'distributor_config': {'relative_path': relative_path}},
            ))
            tuple(api.poll_spawned_tasks(cls.cfg, cls.responses[-1].json()))
            client.response_handler = api.json_handler
            cls.read_paths.append(
                client.get(distributors[1]['_href'])['config']['relative_path']
            )

    def test_status_codes(self):
        """Assert all update requests return an HTTP 202, even if invalid."""
        for i, response in enumerate(self.responses):
            with self.subTest(i=i):
                self.assertEqual(response.status_code, 202)

    def test_successes(self):
        """Assert each valid update can be read back."""
        for i in range(2):
            with self.subTest(i=i):
                self.assertEqual(self.written_paths[i], self.read_paths[i])

    def test_failures(self):
        """Assert each invalid update cannot be read back."""
        if selectors.bug_is_untestable(1106, self.cfg.version):
            self.skipTest('https://pulp.plan.io/issues/1106')
        for i in range(2, len(self.written_paths)):
            with self.subTest(i=i):
                self.assertNotEqual(self.written_paths[i], self.read_paths[i])
