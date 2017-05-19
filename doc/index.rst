Got
===

Got is a utility to clone and manage a set of interdependent git repositories. Build scripts can locate a repository by name, and if the repository isn't already available, it will be silently cloned in the background.

Got has a number of different modes. In general the usage is::

   $ got [-q | --quiet] [--mode] --foo --bar ...

where ``--foo --bar ...`` are mode-specific arguments.

Verbosity
---------

In modes intended to be parsed by a script (:ref:`where <where>`, :ref:`whence <whence>`), verbose information is printed to stderr to keep it separate. In the event of an error, the backtrace that led to the error will be printed to stderr.

To disable this functionality (for example, if the platform calling got combines stdout and stderr into one stream), pass ``--quiet`` (``-q``), or set the environment variable ``GOT_QUIET``.

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

Daemon hosts are hosts running `git-daemon`. When a repository named ``<repo>`` is requested, Got will attempt to clone ``<url>/<repo>``. Note that daemon hosts aren't validated, so if you get the URL wrong all requests will just fail, which Got will interpret as the host not having a repository by that name. If the repository is authenticated, you probably want a URL of the form ``https://username@host``, even though got takes the username separately.

Modes
-----

.. _where:

Find a local repository
~~~~~~~~~~~~~~~~~~~~~~~

Find a repository on disk (or clone it if you don't already have it on disk) using ``--where`` (or ``--local``, whichever you find easier to remember). It's also the default if you specify no mode at all, so the following are equivalent::

   $ got project/repo
   $ got --where project/repo
   $ got --local project/repo

The argument is one or more :ref:`repospecs <repospec>`. Got will output the local path to the requested repositories. In verbose mode, it will mention when a repository is being cloned for the first time.

.. code-block:: text
   :emphasize-lines: 2-5

   $ got project/repo project/repo2
   No local clone on record
   Cloning http://user@localhost:7990/scm/project/repo.git to ~/.got/repos/host/project/repo
   No local clone on record
   Cloning http://user@localhost:7990/scm/project/repo2.git to ~/.got/repos/host/project/repo2
   ~/.got/repos/host/project/repo
   ~/.got/repos/host/project/repo2

Future calls will remember the path to the repository and simply output it::

   $ got project/repo
   ~/.got/repos/host/project/repo

Note that in order to automatically clone repositories, you need to :ref:`add hosts <add-host>` for Got to search.

If the repository isn't already on disk, the ``--on-uncloned`` flag controls what should be done. The possible arguments are:

========  ===========
Argument  Description
========  ===========
clone     Automatically clone the repository. This is the default behavior
skip      Silently stop, printing no path
fail      Raise a fatal error
fake      Print a fake clone path that contains an error string
========  ===========

For example::

    $ got project/repo --on-uncloned=fake
    ~/.got/repos/__REPO_NOT_FOUND__

If you choose to automatically clone a missing repository, you can specify the destination directory with ``--dest``. If omitted, the directory will be chosen based on the :ref:`clone_root <configuration>`, host name, and repo name.

------

Certain extended repospec formats are available only in where mode:

In the case of Bitbucket repositories, you can specify ``project/*`` as a shorthand for all repositories in the specified project. For example, if ``project`` contains two repositories, ``repo1`` and ``repo2``, then the following are equivalent::

    $ got 'project/*'
    $ got project/repo1 project/repo2

This repospec shorthand is only valid with Bitbucket hosts:

.. code-block:: text
   :emphasize-lines: 3

    $ got --add-host host http://localhost --type daemon
    $ got 'host:project/*'
    got --where: error: argument repos: Unable to resolve multipart repospec: host `host' is not a Bitbucket host

If no host is specified, all registered Bitbucket hosts are searched for the specified project.

------

If a list of repospecs is contained within a file (for example, a :ref:`dependency file <dependencies>`), it can be referenced with the repospec ``@filename``. For example, if the file ``foo`` contains the lines ``project/repo1`` and ``project/repo2``, then the following are equivalent::

   $ got @foo project/repo3
   $ got project/repo1 project/repo2 project/repo3

.. _mv:

Move a local repository
~~~~~~~~~~~~~~~~~~~~~~~

Relocate an existing clone on disk with ``--mv``. It takes two arguments, the :ref:`repospec <repospec>` of the repository to move and the target path::

   $ got --mv project/repo ~/new-path
   Moved my-bitbucket:project/repo to ~/new-path

.. _here:

Record/forget a local repository
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you already have a repository cloned on disk, register it with ``--here``. The arguments are a :ref:`repospec <repospec>` and the path to the clone::

   $ got --here my-bitbucket:project/repo ~/my-manual-clone

Normally the host part of a repospec is optional because Got can deduce it, but no host communication is involved in manually registering a clone path, so the host must be specified in the repospec::

   $ got --here project/repo ~/my-manual-clone
   Fatal error: project/repo does not specify the host; it should be of the form <host>:project/repo

Set the path to ``-`` to unregister it from Got. This does not delete the actual clone.

::

   $ got --here my-bitbucket:project/repo -
   my-bitbucket:project/repo no longer has a registered local clone
   (old path still exists on disk: ~/.got/repos/my-bitbucket/project/repo)

.. _whence:

Find a remote repository
~~~~~~~~~~~~~~~~~~~~~~~~

Find which host provides a given repository, without actually cloning it, using ``--whence`` (or ``--remote``). The argument is a :ref:`repospec <repospec>`. This will output the remote clone URL, just as you'd get from running ``git remote show origin`` in a local clone. In verbose mode, it will output each searched host and the error it returned; the search stops as soon as one host returns a match.

.. code-block:: text
   :emphasize-lines: 8-9

   $ got --whence project/repo
   http://user@localhost:7990/scm/project/repo.git

   $ got --whence project/bad-repo


   $ got --whence project/bad-repo
   my-bitbucket: Repository project/bad-repo does not exist
   No valid host has a record of the requested repository

.. _what:

Determine the repository name of a local path
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The opposite of :ref:`--where <where>`, find the name of a repository from its path on disk using ``--what``. The argument is the local clone path. This will output the :ref:`repospec <repospec>` corresponding to that repository. Passing that repospec to ``--where`` will in turn print the path again.

::

   $ got --what ~/.got/repos/host/project/repo
   project/repo

.. _find_root:

Find a repository root
~~~~~~~~~~~~~~~~~~~~~~

Find the root of a got-tracked repository given a path within it using ``--find-root``. The argument is the path to start from, defaulting to the current directory.

.. code-block:: text
   :emphasize-lines: 5

   $ got --find-root ~/.got/repos/host/project/repo/foo/bar/baz
   ~/.got/repos/host/project/repo

   $ got --find-root /dev/null
   Fatal error: `/dev/null' is not within a got repository

.. _deps:

List local dependency paths
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Recursively list the paths to all the repositories the given repository depends on using ``--deps``. The argument is a :ref:`repospec <repospec>`. Dependencies come from a :ref:`dependency file <dependencies>`.  Each dependent repository will be fetched a single time, even when cycles exist in the dependency files.

::

   $ cat $(got project/repo)/deps.got
   project/repo2
   project/repo3

   $ got --deps project/repo
   ~/.got/repos/host/project/repo2
   ~/.got/repos/host/project/repo3

Since this operation is recursive and printing the path to a local clone will cause it to be cloned if not already, running ``--deps`` on a given repospec will ensure that all dependent repos down the tree exist on disk.

.. _git:

Run git command on a repo and its dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Run an arbitrary git command on a repository and the repositories it depends on using ``--git``. There are two optional arguments. ``-C`` (or ``--directory``) can be used to specify the starting repository path; if omitted the current working directory is used. ``-i`` (or ``--ignore-errors``) can be used to continue on through the dependency tree if a particular git invocation fails; otherwise the first failure is a fatal error. All other arguments are passed through to ``git`` directly.

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

The specified git command is run on a given repository before its dependencies are read, so if the command changes the repo's ``deps.got`` file, those changes will take effect immediately.

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

.. _edit-host:

Edit host
~~~~~~~~~

Edit an existing host with ``--edit-host``. The arguments are similar to :ref:`--add-host <add-host>`; ``name`` is mandatory to specify the host, and ``--force`` optionally forces the edit even if unable to connect, just as when adding a host. ``--new-url``, ``--new-username``, and ``--new-password`` all modify the corresponding fields.

Note that when changing the URL, any existing clones from that host are left unchanged, so their remote URLs aren't updated.

.. _rm-host:

Remove host
~~~~~~~~~~~

Remove a host with ``--rm-host``. It takes a single argument, the name of the host::

   $ got --rm-host my-bitbucket
   $ got --hosts
   Name                           Type                 URL

.. _config:

Config
~~~~~~

Get/set configuration keys with ``--config``. If a key and value are passed, the value is stored at that key. If only a key is passed, the current value is printed. If no arguments are passed, all key/value pairs are printed.

See the :ref:`list of configuration keys <configuration>` for more information.

.. _got_root:

Root storage directory
~~~~~~~~~~~~~~~~~~~~~~

By default Got stores its database and cloned repositories in a ``.got`` folder within your home directory. This can be overriden by the ``GOT_ROOT`` environment variable. This is useful if you maintain multiple independent builds on one host, particularly build machines.

.. _dependencies:

Dependencies
------------

A repository can declare a list of the repositories it depends on by listing their :ref:`repospecs <repospec>`, one per line, in a file named ``deps.got`` in the root of the repository. The :ref:`--deps <deps>` and :ref:`--git <git>` commands make use of the dependency list. An example can be found in the :ref:`--deps <deps>` documentation.  The operation will only occur a single time per repository when cycles exist in the dependency graph.

.. _configuration:

Configuration
-------------

The following configuration keys can be read and written with :ref:`--config <config>`:

========================= ============================== ================================================================================
Key                       Default                        Description
========================= ============================== ================================================================================
clone_root                <GOT_ROOT>/repos               Directory to store the cloned repositories in
========================= ============================== ================================================================================
