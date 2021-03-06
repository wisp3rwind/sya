import logging


_MESSAGE_TYPES = [
    'archive_progress',
    'file_status',
    'progress_message',
    'progress_percent',
    'log_message',
    'question_prompt',
    'question_prompt_retry',
    'question_invalid_answer',
    'question_accepted_default',
    'question_accepted_true',
    'question_accepted_false',
    'question_env_answer',
]

_ERROR_MESSAGE_IDS = {
    'Archive.AlreadyExists': "",
    'Archive.DoesNotExist': "",
    'Archive.IncompatibleFilesystemEncodingError': "",
    'Cache.CacheInitAbortedError': "",
    'Cache.EncryptionMethodMismatch': "",
    'Cache.RepositoryAccessAborted': "",
    'Cache.RepositoryIDNotUnique': "",
    'Cache.RepositoryReplay': "",
    'Buffer.MemoryLimitExceeded': "",
    'ExtensionModuleError': "",
    'IntegrityError': "",
    'NoManifestError': "",
    'PlaceholderError': "",
    'KeyfileInvalidError': "",
    'KeyfileMismatchError': "",
    'KeyfileNotFoundError': "",
    'PassphraseWrong': "",
    'PasswordRetriesExceeded': "",
    'RepoKeyNotFoundError': "",
    'UnsupportedManifestError': "",
    'UnsupportedPayloadError': "",
    'NotABorgKeyFile': "",
    'RepoIdMismatch': "",
    'UnencryptedRepo': "",
    'UnknownKeyType': "",
    'LockError': "",
    'LockErrorT': "",
    'ConnectionClosed': "",
    'InvalidRPCMethod': "",
    'PathNotAllowed': "",
    'RemoteRepository.RPCServerOutdated': "",
    'UnexpectedRPCDataFormatFromClient': "",
    'UnexpectedRPCDataFormatFromServer': "",
    'Repository.AlreadyExists': "",
    'Repository.CheckNeeded': "",
    'Repository.DoesNotExist': "",
    'Repository.InsufficientFreeSpaceError': "",
    'Repository.InvalidRepository': "",
    'Repository.AtticRepository': "",
    'Repository.ObjectNotFound': "",
}

_OPERATION_MESSAGE_IDS = {
    'cache.begin_transaction': "Beginning cache transaction",
    'cache.download_chunks': "Downloading chunks cache",
    'cache.commit': "Comitting cache",
    'cache.sync': "Syncing cache",
    'repository.compact_segments': "Compacting repository segments",
    'repository.replay_segments': "Replaying repository segments",
    'repository.check_segments': "Checking repository segments",
    'check.verify_data': "Verifying data",
    'extract': "Extracting",
    'extract.permissions': "",
    'archive.delete': "Deleting archive",
}

_PROMPT_MESSAGE_IDS = {
    'BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK': "",
    'BORG_RELOCATED_REPO_ACCESS_IS_OK': "",
    'BORG_CHECK_I_KNOW_WHAT_I_AM_DOING': "",
    'BORG_DELETE_I_KNOW_WHAT_I_AM_DOING': "",
    'BORG_RECREATE_I_KNOW_WHAT_I_AM_DOING': "",
}

_MESSAGE_IDS = {
    **_ERROR_MESSAGE_IDS,
    **_OPERATION_MESSAGE_IDS,
    **_PROMPT_MESSAGE_IDS,
}


_ALL_EXCEPTIONS = dict()


def make_borg_error(name, msgid):
    e = type(name, (BorgError,), dict())
    _ALL_EXCEPTIONS[msgid] = e
    return e


class BorgError(Exception):
    def __new__(cls, *, message='', msgid=None, **kwargs):
        """ Return a subclass for known errors. Subclasses do not have any
        additional functionality, but are useful for catching only specific
        exceptions.
        """
        if msgid:
            return Exception.__new__(_ALL_EXCEPTIONS[msgid],
                                     message=message,
                                     msgid=msgid,
                                     **kwargs)
        else:
            return super().__new__(message, msgid=msgid, **kwargs)

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __str__(self):
        return self.message


ArchiveAlreadyExists                       = make_borg_error('ArchiveAlreadyExists',                       'Archive.AlreadyExists')
ArchiveDoesNotExist                        = make_borg_error('ArchiveDoesNotExist',                        'Archive.DoesNotExist')
ArchiveIncompatibleFilesystemEncodingError = make_borg_error('ArchiveIncompatibleFilesystemEncodingError', 'Archive.IncompatibleFilesystemEncodingError')
CacheCacheInitAbortedError                 = make_borg_error('CacheCacheInitAbortedError',                 'Cache.CacheInitAbortedError')
CacheEncryptionMethodMismatch              = make_borg_error('CacheEncryptionMethodMismatch',              'Cache.EncryptionMethodMismatch')
CacheRepositoryAccessAborted               = make_borg_error('CacheRepositoryAccessAborted',               'Cache.RepositoryAccessAborted')
CacheRepositoryIDNotUnique                 = make_borg_error('CacheRepositoryIDNotUnique',                 'Cache.RepositoryIDNotUnique')
CacheRepositoryReplay                      = make_borg_error('CacheRepositoryReplay',                      'Cache.RepositoryReplay')
BufferMemoryLimitExceeded                  = make_borg_error('BufferMemoryLimitExceeded',                  'Buffer.MemoryLimitExceeded')
ExtensionModuleError                       = make_borg_error('ExtensionModuleError',                       'ExtensionModuleError')
IntegrityError                             = make_borg_error('IntegrityError',                             'IntegrityError')
NoManifestError                            = make_borg_error('NoManifestError',                            'NoManifestError')
PlaceholderError                           = make_borg_error('PlaceholderError',                           'PlaceholderError')
KeyfileInvalidError                        = make_borg_error('KeyfileInvalidError',                        'KeyfileInvalidError')
KeyfileMismatchError                       = make_borg_error('KeyfileMismatchError',                       'KeyfileMismatchError')
KeyfileNotFoundError                       = make_borg_error('KeyfileNotFoundError',                       'KeyfileNotFoundError')
PassphraseWrong                            = make_borg_error('PassphraseWrong',                            'PassphraseWrong')
PasswordRetriesExceeded                    = make_borg_error('PasswordRetriesExceeded',                    'PasswordRetriesExceeded')
RepoKeyNotFoundError                       = make_borg_error('RepoKeyNotFoundError',                       'RepoKeyNotFoundError')
UnsupportedManifestError                   = make_borg_error('UnsupportedManifestError',                   'UnsupportedManifestError')
UnsupportedPayloadError                    = make_borg_error('UnsupportedPayloadError',                    'UnsupportedPayloadError')
NotABorgKeyFile                            = make_borg_error('NotABorgKeyFile',                            'NotABorgKeyFile')
RepoIdMismatch                             = make_borg_error('RepoIdMismatch',                             'RepoIdMismatch')
UnencryptedRepo                            = make_borg_error('UnencryptedRepo',                            'UnencryptedRepo')
UnknownKeyType                             = make_borg_error('UnknownKeyType',                             'UnknownKeyType')
LockError                                  = make_borg_error('LockError',                                  'LockError')
LockErrorT                                 = make_borg_error('LockErrorT',                                 'LockErrorT')
ConnectionClosed                           = make_borg_error('ConnectionClosed',                           'ConnectionClosed')
InvalidRPCMethod                           = make_borg_error('InvalidRPCMethod',                           'InvalidRPCMethod')
PathNotAllowed                             = make_borg_error('PathNotAllowed',                             'PathNotAllowed')
RemoteRepositoryRPCServerOutdated          = make_borg_error('RemoteRepository.RPCServerOutdated',         'RemoteRepository.RPCServerOutdated')
UnexpectedRPCDataFormatFromClient          = make_borg_error('UnexpectedRPCDataFormatFromClient',          'UnexpectedRPCDataFormatFromClient')
UnexpectedRPCDataFormatFromServer          = make_borg_error('UnexpectedRPCDataFormatFromServer',          'UnexpectedRPCDataFormatFromServer')
RepositoryAlreadyExists                    = make_borg_error('RepositoryAlreadyExists',                    'Repository.AlreadyExists')
RepositoryCheckNeeded                      = make_borg_error('RepositoryCheckNeeded',                      'Repository.CheckNeeded')
RepositoryDoesNotExist                     = make_borg_error('RepositoryDoesNotExist',                     'Repository.DoesNotExist')
RepositoryInsufficientFreeSpaceError       = make_borg_error('RepositoryInsufficientFreeSpaceError',       'Repository.InsufficientFreeSpaceError')
RepositoryInvalidRepository                = make_borg_error('RepositoryInvalidRepository',                'Repository.InvalidRepository')
RepositoryAtticRepository                  = make_borg_error('RepositoryAtticRepository',                  'Repository.AtticRepository')
RepositoryObjectNotFound                   = make_borg_error('RepositoryObjectNotFound',                   'Repository.ObjectNotFound')


_VERBOSITY_OPTIONS = {
    logging.CRITICAL: '--critical',
    logging.ERROR: '--error',
    logging.WARNING: '',
    logging.INFO: '--verbose',
    logging.DEBUG: '--debug',
}

# <name>: [<min-level>, <max-level>, <default-level>]
_COMPRESSION_ALGORITHMS = {
        "none": None,
        "lz4": None,
        "zstd": [1, 22, 3],
        # lzma supports levels 0-9, but according to borg-compression(1),
        #
        #     Giving level 0 (means "no compression", but still has zlib
        #     protocol overhead) is usually pointless, you better use "none"
        #     compression.
        #
        "zlib": [1, 9, 6],
        # lzma supports levels 0-9, but according to borg-compression(1),
        #
        #     Giving levels above 6 is pointless and counter-productive
        #     because it does not compress better due to the buffer size used
        #     by borg [...]
        #
        "lzma": [0, 6, 6],
}
