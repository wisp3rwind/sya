# Overview

[`Sya`][2] is a very basic frontend to [`borg`][1]. Its goal is to
provide easy management of multiple independent backup tasks with an intuitive
commandline interface.

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

 [1]: https://borgbackup.readthedocs.io/
 [2]: https://github.com/niol/sya


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

The backup task section contains a backup task definition. The following
configuration values are accepted in a task file :

* `run-this` : This globally enables or disables the task.
* `repository` : The repository's name as given in the previous section.
* `pre` : execute this in the `bash` shell before executing the backup task.
* `post` : execute this after the backup task.
* `keep` : If any of the subkeys are given, prune the repository after a
  successful backup
  * `hourly` : how many hourly archives to keep when pruning.
  * `daily` : how many daily archives to keep when pruning.
  * `weekly` : how many weekly archives to keep when pruning.
  * `monthly` : how many monthly archives to keep when pruning.
* `prefix` : The prefix . Defaults to `{hostname}`.

The data to backup can either be selected through the files:
* `include-file` : a full path (or relative to the configuration directory)
  to a file that lists what paths to include in the backup.
* `exclude-file` : files to exclude from the backup. See `borg` patterns.

or in-line:
* `includes` :
* `excludes` :

Example task section :

```yaml
    tasks:
      documents:
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
        prefix: {hostname}-documents
        include-file: docs.include
        exclude-file: docs.exclude
        includes:
        - /home/user/.config
        exclude:
        - /home/user/.cache
```

Example exclude file `/etc/sya/local.exclude` :

    /var
    /proc
    /bin
    *.log


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
