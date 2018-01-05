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

.. _multipart_repospec:

Extended form
~~~~~~~~~~~~~

Certain extended repospec formats are available only in a couple modes (for example, :ref:`where mode <where>`).

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

------

If you want to include a repository and all of its :ref:`dependencies <dependencies>`, you can use the form ``repospec+``. For example, if ``project/repo1`` depends on ``project/repo2``, which depends on ``project/repo3``, then the following are equivalent::

   $ got project/repo1+ project/repo4
   $ got project/repo1 project/repo2 project/repo3 project/repo4

Because traversing the dependency list requires all the clones to be on disk, parsing this repospec may cause Got to clone repositories if they're not already available. This happens immediately, before the specified Got command is run.

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

The argument is one or more :ref:`extended repospecs <multipart_repospec>`. Got will output the local path to the requested repositories. In verbose mode, it will mention when a repository is being cloned for the first time.

.. code-block:: text
   :emphasize-lines: 2-5

   $ got project/repo project/repo2
   project/repo: no local clone on record
   Cloning http://user@localhost:7990/scm/project/repo.git to ~/.got/repos/host/project/repo
   project/repo2: no local clone on record
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

If a repository was previously cloned but no longer exists no disk, by default it will be re-cloned to the path Got expected to find it. If you want to avoid this, pass ``--ignore-missing`` and Got will output the expected path to the repository even though it doesn't exist.

.. _where_listen:

Listening for requests
^^^^^^^^^^^^^^^^^^^^^^

Where mode also takes the optional argument ``--listen``. In this mode, Got stays in the foreground after processing all command-line repospecs (which are now optional). Further repospecs can be written to stdin, and the corresponding local paths will be outputted. Got will keep processing valid repospecs until stdin is closed or the process is terminated, but will still exit on fatal error conditions.

.. code-block:: text
   :emphasize-lines: 3-4

   $ echo "project/repo\nproject/repo3" | got --listen
   ~/.got/repos/host/project/repo
   project/repo3: no local clone on record
   Cloning http://user@localhost:7990/scm/project/repo3.git to ~/.got/repos/host/project/repo3
   ~/.got/repos/host/project/repo3

This mode is intended for script usage, and unless you're certain how many paths will be printed for a given repospec, JSON output format is recommended. With JSON output, every request returns a single line containing a JSON list of the paths::

   $ cat $(got project/repo)/deps.got
   project/repo2
   project/repo3

   $ echo "project/repo\nproject/repo+" | got --listen --format json
   [{"repospec": "host:project/repo", "path": "~/.got/repos/host/project/repo"}]
   [{"repospec": "host:project/repo", "path": "~/.got/repos/host/project/repo"}, {"repospec": "host:project/repo2", "path": "~/.got/repos/host/project/repo2"}, {"repospec": "host:project/repo3", "path": "~/.got/repos/host/project/repo3"}]

Recording requests
^^^^^^^^^^^^^^^^^^

To keep a log of all where mode requests, set the environment variable ``GOT_WHERE_LOG``. Got will append requested repospecs to this file as they occur. This can be useful for generating a :ref:`dependency file <dependencies>` for a repository, by building that repository with where logging enabled.

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
   my-bitbucket:project/repo is located at ~/my-manual-clone

If the host is omitted from the repospec, it will be deduced from the origin URL of the target clone::

   $ got --here project/repo ~/my-manual-clone
   No host specified -- searching for one with clone URL http://user@localhost:7990/scm/project/repo.git
   Deduced host my-bitbucket
   my-bitbucket:project/repo is located at ~/my-manual-clone

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

List local dependency info
~~~~~~~~~~~~~~~~~~~~~~~~~~

Recursively list information about all the repositories the given repository depends on using ``--deps``. The arguments are an optional :ref:`repospec <repospec>` and format for the information to take. By default the current repository is used, and the format is ``%p``. Dependencies come from a :ref:`dependency file <dependencies>`. By default the first file queried is ``deps.got`` in the current repository's root, but this can be overriden with ``--file``. Each dependent repository will be fetched a single time, even when cycles exist in the dependency files.

The format specifier loosely models the "pretty formats" used by commands like `git show` and `git log`. The following placeholders are available:

=========== ========================================== ========================================
Placeholder Description                                Example
=========== ========================================== ========================================
``%H``      Hash of the current head                   4b825dc642cb6eb9a060e54bf8d69288fbee4904
``%h``      Short hash of the current head             4b825dc
``%RS``     Repospec                                   my-bitbucket:project/repo@master
``%rs``     Abbreviated repospec (no host or revision) project/repo
``%p``      Path                                       ~/.got/repos/host/project/repo
=========== ========================================== ========================================

For example::

   $ cat $(got project/repo)/deps.got
   project/repo2
   project/repo3

   $ got --deps project/repo
   ~/.got/repos/host/project/repo
   ~/.got/repos/host/project/repo2
   ~/.got/repos/host/project/repo3

   $ got --deps project/repo --format "%rs's short hash is %h"
   project/repo's short hash is dbbc5d8
   project/repo2's short hash is 10bac04
   project/repo3's short hash is a34a873

Since this operation is recursive and fetching clone information causes it to be cloned if not already, running ``--deps`` on a given repospec will ensure that all dependent repos down the tree exist on disk.

Note that the current repository is included in the output, as many use cases involve operating on the repository as well as its dependencies.

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

.. _run:

Run arbitrary command on specified repositories
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Run an arbitrary command on a list of :ref:`extended repospecs <multipart_repospec>`. There are two optional arguments. ``--bg`` can be used to run the commands in the background in parallel; by default each invocation will be allowed to finish before the next begins. ``-i`` (or ``--ignore-errors``) can be used to continue on through the repository list if a particular invocation fails; otherwise the first failure is a fatal error. ``--bg`` implies ``--ignore-errors`` since the invocations run simultaneously.

There is also one required argument, ``-x`` (or ``--cmd``). This is to specify where the command begins, and so must be the last argument.

For example::

   $ got project/repo project/repo2 --bg -x make -j8

Got will exit 0 if all invocations were successful. If an invocation failed in foreground mode, Got exits 1 immediately. Otherwise Got will finish the other invocations and exit with the total number that failed. Note that Got will exit non-zero on invocation failure even with ``--ignore-errors`` -- this flag is just to prevent bailing out early.

.. _prune:

Cleanup removed repositories
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Scan the filesystem for clones that no longer exist with ``--prune``. Like :ref:`here mode <here>` with a path of `-`, this unregisters clones so that future lookups will make a fresh clone. In the case of ``--prune``, every clone is checked to see if it still exists on disk, and all missing clones are removed. There is one optional argument, ``-i`` (or ``--interactive``), which prompts to unregister each missing clone.

.. _hosts:

List hosts
~~~~~~~~~~

List all registered hosts with ``--hosts``::

   $ got --hosts
             Name: my-bitbucket
             Type: bitbucket
              URL: http://localhost:7990/
         Username: user
     SSH key path: None
        Clone URL: None
       Clone root: <global> ~/.got/repos/my-bitbucket
     Total clones: 0

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
``--password [PASSWORD]`` Optional   Account password. Optional if no authentication is required or you're using an SSH key. Use ``--password`` with no password to be prompted for one on stdin
``--ssh-key PEM_FILE``    Optional   Path to SSH private key. Optional if no authentication is required or you're using a password
``--clone-url URL``       Optional   Pattern to use to figure out a clone URL for a given repospec
``--clone-root PATH``     Optional   Directory to store new clones in. By default this is a subdirectory of the :ref:`global clone root <configuration>`, named the same as the host
``--force``               Optional   Add the host even if unable to connect to it
========================= ========== ======================================================

::

   $ got --add-host my-bitbucket http://localhost:7990/ -u user -p
   Password: 
   Added bitbucket host bitbucket at http://localhost:7990/
   $ got --hosts
             Name: my-bitbucket
             Type: bitbucket
              URL: http://localhost:7990/
         Username: user
     SSH key path: None
        Clone URL: None
       Clone root: <global> ~/.got/repos/my-bitbucket
     Total clones: 0

There are multiple authentication options depending on the host configuration:

* If the host doesn't require authentication, all of the authentication options can be omitted.
* If the host requires a username and password, use ``--username`` and ``--password``.
* If the host requires an SSH key, use ``--ssh-key``.

  * In the case of Bitbucket hosts, the SSH key doesn't provide API access, so features requiring the API will be disabled. This includes host validation (making sure you have access to the host at creation time) and glob repospecs (e.g. `project/*`). If you provide both a username/password and an SSH key, the SSH key will be used for cloning but the password will be used for API access.

Both host types will automatically determine the clone URL given the host's base URL and the desired repospec. Bitbucket hosts use the API to request the clone URL, while daemon hosts simply concatenate the base URL and the repospec. If this is not the correct scheme to follow, or if you have a Bitbucket host with no API access because you're using SSH keys, you can specify the clone URL scheme using ``--clone-url``. This is a format string that accepts the following placeholders:

============= ==========================================
Placeholder   Description
============= ==========================================
``%rs``       The requested repospec
``%username`` The username associated with the host
============= ==========================================

::

   $ got --add-host bitbucket http://localhost:7990/ --ssh-key ~/.ssh/id_rsa --clone-url 'ssh://git@localhost:7999/%rs.git'
   Added bitbucket host bitbucket at http://localhost:7990/

.. _edit-host:

Edit host
~~~~~~~~~

Edit an existing host with ``--edit-host``. The arguments are similar to :ref:`--add-host <add-host>`; ``name`` is mandatory to specify the host, and ``--force`` optionally forces the edit even if unable to connect, just as when adding a host. ``--set-url``, ``--set-username``, ``--set-password``, ``--set-ssh-key``, ``--set-clone-url``, and ``--set-clone-root`` all modify the corresponding fields.

The options ``--set-url``, ``--set-ssh-key``, and ``--set-clone-url`` require special care because they can change what URL clones expect to originate from. If you have existing clones from this host that need to be updated, use ``--update-clones`` to recompute their origin URLs and update the repository remotes.

.. _rm-host:

Remove host
~~~~~~~~~~~

Remove a host with ``--rm-host``. It takes a single argument, the name of the host::

   $ got --rm-host my-bitbucket
   $ got --hosts
   No hosts configured

.. _config:

Config
~~~~~~

Get/set configuration keys with ``--config``. If a key and value are passed, the value is stored at that key. If only a key is passed, the current value is printed. If no arguments are passed, all key/value pairs are printed.

See the :ref:`list of configuration keys <configuration>` for more information.

.. _got_root:

Root storage directory
~~~~~~~~~~~~~~~~~~~~~~

By default Got stores its database and cloned repositories in a ``.got`` folder within your home directory. This can be overriden by the ``GOT_ROOT`` environment variable. This is useful if you maintain multiple independent builds on one host, particularly build machines.

Make a temporary workspace
~~~~~~~~~~~~~~~~~~~~~~~~~~

Create a new temporary workspace with ``--worktree``. Analogous to git worktrees, this makes a new temporary directory in which to test things in a clean environment, and opens a new shell in that directory. The new environment has a separate :ref:`got root <got_root>`, but inherits many of the settings from the main Got, including all of its hosts, but notably not its clones. This means you can immediately start making new clones via :ref:`where mode <where>`.

The ``--worktree`` command changes once you're inside a worktree, to change properties of the current worktree instead of making a new one. Both versions take a number of optional arguments.

When creating a worktree, the directory to use can be specified with ``-d`` (or ``--dir``). This directory must be empty (or not exist at all).

To automatically delete the directory when the shell exits, use ``-t`` (or ``--temp``); otherwise it will be left on disk (but by default is stored in a system-specified temporary location like `/tmp`). Once inside a worktree, you can change this setting with ``--keep`` or ``--delete``. You can also avoid deleting a worktree that was marked for deletion by exiting its shell non-zero::

   ~ $ got --worktree -t
   Making temporary worktree shell at /tmp/got_worktree_4jpmry1a

   (worktree) /tmp/got_worktree_4jpmry1a $ exit
   Cleaning up worktree

   ~ $ got --worktree -t
   Making temporary worktree shell at /tmp/got_worktree_6btvmqpc

   (worktree) /tmp/got_worktree_6btvmqpc $ exit 1
   Temporary worktree exited 1; preserving contents

   ~ $ got --worktree -t
   Making temporary worktree shell at /tmp/got_worktree_2i1fy0sb

   (worktree) /tmp/got_worktree_2i1fy0sb $ got --worktree --keep
   Worktree flagged for retention on exit

   (worktree) /tmp/got_worktree_2i1fy0sb $ exit

   ~ $

To include some or all of the parents clones in the worktree, use ``-r`` (or ``--with-repos`` on creation, ``--import-repos`` within an existing worktree). This makes entries in the worktree's clone list, pointing at the parent's clones. Note that this means changes to those clones will change the parent; Got does not undo these changes on worktree cleanup. If you specify one or more repospecs, e.g. ``-r project/repo1 project/repo2``, those clones will be imported from the parent (if they exist). You can use ``*`` anywhere in these repospecs to match many, e.g. ``project/*`` or even ``*j*/*3``. If you specify ``-r`` with no repospecs, all clones are imported from the parent.

::

   ~ $ got project/repo1 project/repo2
   ~/.got/repos/host/project/repo1
   ~/.got/repos/host/project/repo2

   ~ $ got --worktree -r project/repo1
   Making worktree shell at /tmp/got_worktree_40wxvzdw

   (worktree) /tmp/got_worktree_40wxvzdw $ got project/repo1
   ~/.got/repos/host/project/repo1

   (worktree) /tmp/got_worktree_40wxvzdw $ got project/repo2
   project/repo2: no local clone on record
   Cloning http://user@localhost:7990/scm/project/repo2.git to /tmp/got_worktree_40wxvzdw/repos/host/project/repo2
   /tmp/got_worktree_40wxvzdw/repos/host/project/repo2

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

Emacs integration
-----------------

Here is an emacs function that takes a repospec and returns the corresponding local clone path:

.. code-block:: elisp

   (defun got-lookup (repospec)
     (with-temp-buffer
      (let ((ret (call-process "got"
                               nil
                               (current-buffer)
                               nil
                               "-q"
                               (shell-quote-argument repospec)))
            (stdout (replace-regexp-in-string "\n\\'" "" (buffer-string))))
        (if (zerop ret)
          (format "%s/" stdout)
          (error "Got lookup failed: %s" stdout)))))

One way to use this function is by binding a find-file hotkey to read the repospec from a minibuffer and paste the resulting path into the find-file prompt:

.. code-block:: elisp

   (define-key minibuffer-local-filename-completion-map (kbd "@") (lambda () (interactive) (insert (got-lookup (read-from-minibuffer "Got: ")))))

As demonstrated here:

.. image:: emacs.gif
