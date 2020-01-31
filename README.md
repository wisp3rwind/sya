# Overview

[`Sya`][sya-wisperwind] is a very basic frontend to [`borg`][borg-docs].
Its goal is to provide easy management of multiple independent backup tasks
with an intuitive command-line interface.

 [borg-docs]: https://borgbackup.readthedocs.io/
 [sya-niol]: https://github.com/niol/sya
 [sya-wisperwind]: https://github.com/wisp3rwind/sya


## Under the hood & historical remarks
Basically, it constructs `borg` command lines from repository and task
specifications given in a configuration file. Then, the JSON interface to
`borg`'s output is used to build a humanly-manageable tool.

While this started as a fork of @niol's tool, there's essentially no
compatibility to his original version and almost all of the code has seen
significant changes:
- **this README is hopelessly out of date**
- the configuration file is YAML instead of INI
- the CLI is entirely different (subcommands, etc.)
- it is very much WIP, breaking changes are expected

Some features that I would like to implement in the future include
- [ ] GUI (in addition to the commandline)
- [ ] Desktop notifications
- [ ] tray icon with status similar to time machine
- [ ] better integration with btrfs snapshots


## Configuration

### General

The configuration directory (default is `/etc/sya`) contains the main
configuration file `config.yaml`. Its content is split into three section:
`sya`, `repositories` and `tasks`.

### `sya` section

This section makes it easy to configure general runtime items.

```yaml
    sya:
      verbose: True
```

### `repositories` section
* `repository` : the path to the repository to backup to. Prefix with `host:` to backup over SSH.
* `passphrase-file` : a file containing on the first line the passphrase used
  to encrypt the backup repository (`borg init -e repokey`)
* `remote-path` : the path to the borg executable on the remote machine.

### Backup `tasks` section

The backup task section contains a mapping of task names to _task objects_.
Each task object specifies its source files and a repository to backup to.

#### Task objects
Task object support the following configuration values:

* `run-this: [yes|no]` : This globally enables or disables the task.
* `repository: [repository name]` : The repository's name as given in the previous section.
* `pre` : execute this in the shell before executing the backup task.
* `post` : execute this after the backup task.
* `keep` : Specifies the retention policy for the archives from this task. If
  provided, the archives will be pruned after a successful backup or on manual
  invocation of the `prune` sub-command.
  Can either contain the keys-value pairs
  * `within` : how many yearly archives to keep when pruning,
  * `secondly` : how many secondly archives to keep when pruning,
  * `minutely` : how many minutely archives to keep when pruning,
  * `hourly` : how many hourly archives to keep when pruning,
  * `daily` : how many daily archives to keep when pruning,
  * `weekly` : how many weekly archives to keep when pruning,
  * `monthly` : how many monthly archives to keep when pruning,
  * `yearly` : how many yearly archives to keep when pruning,
  or a sequence of such mappings. In the latter case, `borg prune` will be
  run multiple times. See below for an example of a retention policy that
  can only be specified in this way. For the exact meaning of these keys,
  consult [`borg-prune(1)`][man-1-borg-prune].
* `prefix` : The prefix for archive names. Defaults to `{hostname}`.

The data to backup can either be selected through the files:
* `include-file` : a full path (or relative to the configuration directory)
  to a file that lists what paths to include in the backup,
* `exclude-file` : files to exclude from the backup. See `borg` patterns.

or in-line:
* `includes` : list of files,
* `excludes` : list of files.

 [man-1-borg-prune]: https://borgbackup.readthedocs.io/en/stable/usage/prune.html

Example task object :

    ```yaml
    backup-my-computer:
      run-this: yes
      repository: mydisk
      pre: 
      - rm-temp.sh
      - mount-mydisk-rw.sh
      post: mount-mydisk-ro.sh
      keep:
        hourly: 24
        daily: 8
        weekly: 8
        monthly: 12
      prefix: my-computer
      include-file: my-computer.include
      exclude-file: my-computer.exclude
      includes:
      - /home/user/.config
      exclude:
      - /home/user/.cache
    ```

Example include file `/etc/sya/my-computer.include` :

    ```
    /etc
    /var
    /home
    ```

Example exclude file `/etc/sya/my-computer.exclude` :

    ```
    /var/log
    /var/cache
    *.log
    ```

As a shortcut, you could also use a combined include file using the `- ` prefix

    ```
    /etc
    /var
    /home
    - /var/log
    - /var/cache
    - *.log
    ```

Another example task object, which makes use of a more sophisticated retention
scheme:

    ```yaml
    backup-my-computer:
      run-this: yes
      repository: mydisk
      keep:
        # Beyond one year, keep only monthly archives for up to 5 years
        - within: 1y
          monthly: 120
        # Beyond 2 months, keep only weekly archives.
        - within: 2m
          weekly: 1000000
        # Beyond 2 weeks, keep only daily archives.
        - within: 2w
          daily: 1000000
        # Within 2 weeks, allow hourly archives.
        - hourly: 1000000
      include-file: my-computer.include
    ```

## Usage

`sya` accepts the following command-line options :


## Installation (short)

`sya` is a python package, as such the easiest way to install it is through
`pip`. The package also includes an example configuration file.
There's an Arch PKGBUILD on the [AUR](???).

I automate backups through `systemd` timers and services, corresponding example
files are also included. It should be straightforward to run `sya` through
cron, `too`, but I haven't done that.


# Similar Tools (a.k.a. inspiration for new features)
* [`borg-gtk`][borg-gtk]: GUI, JSON interface
* [`borgcube`][borgcube]
* [`borgjs`][borgjs]
* [`borg-web`][borg-web]
* [`borg_notifications_multi_target`][borg_notifications_multi_target]
* [`borg-notify`][borg-notify] Desktop notifications

 [borg-gtk]: https://github.com/Abogical/borg-gtk
 [borgcube]: https://github.com/enkore/borgcube
 [borgjs]: https://github.com/vesparny/borgjs
 [borg-web]: https://github.com/borgbackup/borgweb
 [borg_notifications_multi_target]: https://github.com/lhupfeldt/borg_notifications_multi_target
 [borg-notify]: https://github.com/PhrozenByte/borg-notify
