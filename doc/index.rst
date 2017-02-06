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

.. WARNING:: Version pinning is not yet implemented; a repospec with a unique version will be managed separately but still synced to HEAD

.. _where:

Find a local repository
-----------------------

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
------------------------

You can find which host provides a given repository, without actually cloning it, using ``--whence`` (or ``--remote``). The argument is a :ref:`repospec <repospec>`. This will output the remote clone URL, just as you'd get from running ``git remote show origin`` in a local clone. In verbose mode, it will output each searched host and the error it returned; the search stops as soon as one host returns a match.

.. code-block:: text
   :emphasize-lines: 8-9

   $ got --whence project/repo
   http://user@localhost:7990/scm/project/repo.git

   $ got --whence project/bad-repo


   $ got -v --whence project/bad-repo
   my-bitbucket: Repository project/bad-repo does not exist
   No valid host has a record of the requested repository

.. _hosts:

List hosts
----------

List all registered hosts with ``--hosts``::

   $ got --hosts
   Name                           Type                 URL
   my-bitbucket                   bitbucket            http://localhost:7990/

.. _add-host:

Add host
--------

Add a new host with ``--add-host``. It takes a number of arguments:

========================= ========== ======================================================
Argument                  Type       Description
========================= ========== ======================================================
``name``                  Mandatory  Friendly name of the host
``url``                   Mandatory  Root URL of the host
``--type TYPE``           Optional   Host type. Currently only ``bitbucket`` is supported
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
-----------

Remove a host with ``--rm-host``. It takes a single argument, the name of the host::

   $ got --rm-host my-bitbucket
   $ got --hosts
   Name                           Type                 URL
