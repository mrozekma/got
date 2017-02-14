Got
===

Got is a utility to clone and manage a set of interdependent git repositories. Build scripts can locate a repository by name, and if the repository isn't already available, it will be silently cloned in the background.

Got has a number of different modes. In general the usage is::

   $ got [-v | --verbose] [--mode] --foo --bar ...

where ``--foo --bar ...`` are mode-specific arguments.

Verbosity
---------

Pass ``--verbose`` (``-v``) to enable verbose output. In modes intended to be parsed by a script (:ref:`where <where>`, :ref:`whence <whence>`), the verbose information will be printed to stderr to keep it separate. In the event of an error, the backtrace that led to the error will be printed to stderr.

.. role:: stderr-example

In the following examples, stderr is printed in :stderr-example:`red` to distinguish it from stdout.

.. _repospec:

Repository specifications
-------------------------

Several modes take an argument type they refer to as a `repository specification`. These are of the form::

   host:name@version

.. TODO Why is '@version' bold here?

Only ``name`` is mandatory; if ``host`` is omitted all known hosts will be searched, and if ``version`` is omitted the latest version of the repository is pulled. ``version`` is used if the caller requires a particular version of the repository; the clone will be checked out to that refspec and not updated.

.. _host_types:

Host types
----------

Bitbucket
~~~~~~~~~

``--type=bitbucket``

A Bitbucket URL should be the root of the bitbucket installation. By default this is something like ``http://hostname:7990/``, but might contain a subdirectory depending on how Bitbucket was setup. It should *not* contain ``/projects``. The connection will be validated when the host is first added to make sure the URL is valid and the username/password is correct.

Daemon
~~~~~~

``--type=daemon``

Daemon hosts are hosts running `git-daemon`. When a repository named ``<repo>`` is requested, Got will attempt to clone ``<url>/<repo>``. Note that daemon hosts aren't validated, so if you get the URL wrong all requests will just fail, which Got will interpret as the host not having a repository by that name.

Modes
-----

.. _where:

Find a local repository
~~~~~~~~~~~~~~~~~~~~~~~

Find a repository on disk (or clone it if you don't already have it on disk) using ``--where`` (or ``--local``, whichever you find easier to remember). It's also the default if you specify no mode at all, so the following are equivalent::

   $ got project/repo
   $ got --where project/repo
   $ got --local project/repo

The argument is a :ref:`repospec <repospec>`. Got will output the local path to the requested repository. In verbose mode, it will mention when a repository is being cloned for the first time.

.. code-block:: text
   :emphasize-lines: 2-3

   $ got -v project/repo
   No local clone on record
   Cloning http://user@localhost:7990/scm/project/repo.git to ~/.got/repos/host/project/repo
   ~/.got/repos/host/project/repo

Future calls will remember the path to the repository and simply output it::

   $ got -v project/repo
   ~/.got/repos/host/project/repo

Note that in order to automatically clone repositories, you need to :ref:`add hosts <add-host>` for Got to search.

.. _whence:

Find a remote repository
~~~~~~~~~~~~~~~~~~~~~~~~

Find which host provides a given repository, without actually cloning it, using ``--whence`` (or ``--remote``). The argument is a :ref:`repospec <repospec>`. This will output the remote clone URL, just as you'd get from running ``git remote show origin`` in a local clone. In verbose mode, it will output each searched host and the error it returned; the search stops as soon as one host returns a match.

.. code-block:: text
   :emphasize-lines: 8-9

   $ got --whence project/repo
   http://user@localhost:7990/scm/project/repo.git

   $ got --whence project/bad-repo


   $ got -v --whence project/bad-repo
   my-bitbucket: Repository project/bad-repo does not exist
   No valid host has a record of the requested repository

.. _what:

Determine the repository name of a local path
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The opposite of :ref:`--where <where>`, find the name of a repository from its path on disk using ``--what``. The argument is the local clone path. This will output the :ref:`repospec <repospec>` corresponding to that repository. Passing that repospec to ``--where`` will in turn print the path again.

::

   $ got --what ~/.got/repos/host/project/repo
   project/repo

.. _deps:

List local dependency paths
~~~~~~~~~~~~~~~~~~~~~~~~~~~

List the paths to all the repositories the given repository depends on using ``--deps``. The argument is a :ref:`repospec <repospec>`. Dependencies come from a :ref:`dependency file <dependencies>`.

::

   $ cat $(got project/repo)/deps.got
   project/repo2
   project/repo3

   $ got --deps project/repo
   ~/.got/repos/host/project/repo2
   ~/.got/repos/host/project/repo3

.. _git:

Run git command on a repo and its dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Run an arbitrary git command on a repository and the repositories it depends on using ``--git``. There is one optional argument, ``-C`` (or ``--directory``), to specify the starting repository path; if omitted the current working directory is used. All other arguments are passed through to ``git`` directly.

::

   $ got --git -C $(got project/repo) status
   my-bitbucket:project/repo
   On branch master
   Your branch is up-to-date with 'origin/master'.
   nothing to commit, working directory clean

   my-bitbucket:project/repo2
   On branch master
   Your branch is up-to-date with 'origin/master'.
   nothing to commit, working directory clean

   my-bitbucket:project/repo3
   On branch master
   Your branch is up-to-date with 'origin/master'.
   nothing to commit, working directory clean

Repositories pinned to a particular version are treated specially in this mode. Since these repositories are expected to remain static, a warning is printed if there are any uncommitted changes or if the repository's head no longer points to the pinned version. Got won't attempt to fix this, but you should look into it manually to figure out why the repository is in the wrong state. To help prevent this situation, certain git commands are treated specially when run on pinned repositories:

============  ================================================================================
Command       Pinned behavior
============  ================================================================================
commit, push  The repository is skipped; no command is run
fetch, pull   Commits are fetched from the origin and head is hard-reset to the pinned version
============  ================================================================================


.. _hosts:

List hosts
~~~~~~~~~~

List all registered hosts with ``--hosts``::

   $ got --hosts
   Name                           Type                 URL
   my-bitbucket                   bitbucket            http://localhost:7990/

.. _add-host:

Add host
~~~~~~~~

Add a new host with ``--add-host``. It takes a number of arguments:

========================= ========== ======================================================
Argument                  Type       Description
========================= ========== ======================================================
``name``                  Mandatory  Friendly name of the host
``url``                   Mandatory  Root URL of the host
``--type TYPE``           Optional   Host type; see the :ref:`list of host types <host_types>` for more info. Defaults to ``bitbucket``
``--username USERNAME``   Optional   Account username. Optional if no authentication is required
``--password [PASSWORD]`` Optional   Account password. Optional if no authentication is required. Use ``--password`` with no password to be prompted for one on stdin
``--force``               Optional   Add the host even if unable to connect to it
========================= ========== ======================================================

::

   $ got --add-host my-bitbucket http://localhost:7990/ -u user -p
   Password: 
   $ got --hosts
   Name                           Type                 URL
   my-bitbucket                   bitbucket            http://localhost:7990/

.. _rm-host:

Remove host
~~~~~~~~~~~

Remove a host with ``--rm-host``. It takes a single argument, the name of the host::

   $ got --rm-host my-bitbucket
   $ got --hosts
   Name                           Type                 URL

.. _dependencies:

Dependencies
------------

A repository can declare a list of the repositories it depends on by listing their :ref:`repospecs <repospec>`, one per line, in a file named ``deps.got`` in the root of the repository. The :ref:`--deps <deps>` and :ref:`--git <git>` commands make use of the dependency list. An example can be found in the :ref:`--deps <deps>` documentation.
