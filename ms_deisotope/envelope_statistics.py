import operator


intensity_getter = operator.attrgetter("intensity")
mz_getter = operator.attrgetter("mz")
snr_getter = operator.attrgetter("signal_to_noise")


def a_to_a2_ratio(envelope):
    if len(envelope) < 3:
        return 0.
    a0 = envelope[0]
    a2 = envelope[2]
    if a0.mz < 0 or a2.mz < 0:
        return 0.
    return a0.intensity / a2.intensity


def most_abundant_mz(envelope):
    return max([p for p in envelope if p.mz > 1], key=intensity_getter).mz


def average_mz(envelope):
    envelope = [p for p in envelope if p.mz > 1]
    return sum(map(mz_getter, envelope)) / float(len(envelope))


def average_signal_to_noise(envelope):
    envelope = [p for p in envelope if p.mz > 1]
    return sum(map(snr_getter, envelope)) / float(len(envelope))