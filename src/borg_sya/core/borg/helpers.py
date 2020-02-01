# Taken from the borgbackup source (MIT)

def format_file_size(v, precision=2, sign=False):
    """Format file size into a human friendly format
    """
    return sizeof_fmt_decimal(v, suffix='B', sep=' ', precision=precision, sign=sign)


def sizeof_fmt(num, suffix='B', units=None, power=None, sep='', precision=2, sign=False):
    prefix = '+' if sign and num > 0 else ''

    for unit in units[:-1]:
        if abs(round(num, precision)) < power:
            if isinstance(num, int):
                return "{}{}{}{}{}".format(prefix, num, sep, unit, suffix)
            else:
                return "{}{:3.{}f}{}{}{}".format(prefix, num, precision, sep, unit, suffix)
        num /= float(power)
    return "{}{:.{}f}{}{}{}".format(prefix, num, precision, sep, units[-1], suffix)


def sizeof_fmt_iec(num, suffix='B', sep='', precision=2, sign=False):
    return sizeof_fmt(num, suffix=suffix, sep=sep, precision=precision, sign=sign,
                      units=['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi', 'Yi'], power=1024)


def sizeof_fmt_decimal(num, suffix='B', sep='', precision=2, sign=False):
    return sizeof_fmt(num, suffix=suffix, sep=sep, precision=precision, sign=sign,
                      units=['', 'k', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y'], power=1000)

