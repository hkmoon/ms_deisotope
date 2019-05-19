'''Represent the basic structures of a mass spectrum and its processed contents,
and provide an interface for manipulating that data.
'''
import warnings

from collections import namedtuple
from numbers import Number

import numpy as np

from ms_peak_picker import average_signal
from ms_peak_picker.base import PeakLike

from ms_deisotope.averagine import neutral_mass, mass_charge_ratio
from ms_deisotope.deconvolution import deconvolute_peaks

try:
    from ms_deisotope.plot import annotate_scan as _annotate_precursors, draw_raw
except ImportError:
    def _missing_matplotlib(*args, **kwargs):
        raise ImportError(
            "This method requires matplotlib. Please install it.")
    _annotate_precursors = _missing_matplotlib
    draw_raw = _missing_matplotlib

from ms_deisotope.utils import Constant


DEFAULT_CHARGE_WHEN_NOT_RESOLVED = 1
ChargeNotProvided = Constant("ChargeNotProvided")


class ScanBunch(namedtuple("ScanBunch", ["precursor", "products"])):
    """Represents a single MS1 scan and all MSn scans derived from it,
    or a collection of related MSn scans.

    Attributes
    ----------
    precursor: :class:`~.ScanBase`
        A single MS1 scan which may have undergone MSn
    products: list
        A list of 0 or more :class:`~.ScanBase` objects which were derived
        from :attr:`precursor` or another element of this list derived
        from it.
    """

    def __new__(cls, *args, **kwargs):  # pylint: disable=super-on-old-class
        inst = super(ScanBunch, cls).__new__(cls, *args, **kwargs)
        inst._id_map = {}
        if inst.precursor is not None:
            inst._id_map[inst.precursor.id] = inst.precursor
        for scan in inst.products:
            inst._id_map[scan.id] = scan
        return inst

    def precursor_for(self, scan):
        """Find the precursor :class:`~.ScanBase` instance
        for the given scan object

        Parameters
        ----------
        scan : :class:`~.ScanBase`
            The MSn scan to look for the MSn-1 scan for

        Returns
        -------
        :class:`~.ScanBase`
        """
        if scan.precursor_information is not None:
            scan_id = scan.precursor_information.precursor_scan_id
            return self.get_scan_by_id(scan_id)
        return None

    def get_scan_by_id(self, scan_id):
        """Retrieve the scan object for the specified scan id from this
        group in memory.

        Parameters
        ----------
        scan_id : str
            The unique scan id value to be retrieved

        Returns
        -------
        :class:`~.ScanBase`
        """
        return self._id_map[scan_id]

    def annotate_precursors(self, nperrow=4, ax=None):
        '''Plot the spectra in this group as a grid, with the full
        MS1 spectrum in profile in the top row, and each MSn spectrum's
        precursor ion revealed in a grid panel below, with isolation
        window and selected ion/monoisotopic peak annotated.

        Parameters
        ----------
        nperrow: :class:`int`
            The number of precursors to annotate per row
            in the grid.
        ax: :class:`matplotlib._axes.Axes`, optional
            The axis to draw on. If not provided, a new figure
            will be created, along with a new axis.

        Returns
        -------
        :class:`matplotlib._axes.Axes`
        '''
        return _annotate_precursors(
            self.precursor, self.products, nperrow=nperrow, ax=ax)

    def _repr_pretty_(self, p, cycle):  # pragma: no cover
        if cycle:
            p.text("ScanBunch(...)")
            return
        p.text("ScanBunch(\n")
        with p.group(2):
            with p.group(4, "precursor=\n"):
                p.pretty(self.precursor)
            with p.group(4, ",\nproducts=\n"):
                p.pretty(self.products)
        p.text(")")

    def pack(self):
        '''Build a new :class:`ScanBunch` where each scan in it is returned by calling
        :meth:`~.Scan.pack`

        Returns
        -------
        :class:`ScanBunch`
        '''
        return self.__class__(self.precursor.pack(), [
            p.pack() for p in self.products
        ])


class RawDataArrays(namedtuple("RawDataArrays", ['mz', 'intensity'])):
    """Represent the m/z and intensity arrays associated with a raw
    mass spectrum.

    Supports scaling and summing, as well as low level m/z search.

    Thin wrapper around a ``namedtuple``, so this object supports
    the same interfaces as a tuple.

    Attributes
    ----------
    mz: np.ndarray
        The m/z axis of a mass spectrum
    intensity: np.ndarray
        The intensity measured at the corresponding m/z of a mass spectrum
    """

    def __new__(cls, mz, intensity, arrays=None):
        inst = super(RawDataArrays, cls).__new__(cls, mz, intensity)
        inst.data_arrays = dict()
        if arrays:
            inst.data_arrays.update(arrays)
        return inst

    def __copy__(self):
        inst = self.__class__(self.mz.copy(), self.intensity.copy(), {
            k: v.copy() for k, v in self.data_arrays.items()
        })
        return inst

    def copy(self):
        """Make a deep copy of this object.

        Returns
        -------
        :class:`RawDataArray`
        """
        return self.__copy__()

    def plot(self, *args, **kwargs):
        """Draw the profile spectrum described by the
        contained arrays.

        Parameters
        ----------
        ax: :class:`matplotlib._axes.Axes`
            The figure axes onto which to draw the plot. If not provided,
            this will default to the current figure interactively.
        **kwargs
            All keywords are forwarded to :meth:`plot` on ``ax``.

        Returns
        -------
        :class:`matplotlib._axes.Axes`
            The axes drawn on
        """
        ax = draw_raw(self, *args, **kwargs)
        return ax

    def __eq__(self, other):
        try:
            return np.allclose(
                self[0], other[0]) and np.allclose(
                    self[1], other[1])
        except ValueError:
            return False

    def __ne__(self, other):
        return not (self == other)

    def __mul__(self, i):
        return self.__class__(self.mz, self.intensity * i)

    def __div__(self, d):
        return self.__class__(self.mz, self.intensity / d)

    def __add__(self, other):
        if len(self.mz) == len(other.mz) and np.allclose(self.mz, other.mz):
            return self.__class__(self.mz, self.intensity + other.intensity)
        else:
            return self.__class__(*average_signal([self, other])) * 2

    def find_mz(self, mz):
        """Find the nearest index to the query ``mz``

        Parameters
        ----------
        mz : float
            The m/z value to search for

        Returns
        -------
        int
            The index nearest to the query m/z
        """
        n = len(self.mz)
        lo = 0
        hi = n

        while hi != lo:
            mid = int((hi + lo) // 2)
            y = self.mz[mid]
            err = y - mz
            if abs(err) < 0.1:
                best_index = mid
                best_err = abs(err)
                i = mid
                while i >= 0:
                    y = self.mz[i]
                    err = y - mz
                    if err <= -0.1:
                        break
                    abs_err = abs(err)
                    if abs_err < best_err:
                        best_err = abs_err
                        best_index = i
                    i -= 1
                i = mid
                while i < n:
                    y = self.mz[i]
                    err = y - mz
                    if err >= 0.1:
                        break
                    abs_err = abs(err)
                    if abs_err < best_err:
                        best_err = abs_err
                        best_index = i
                    i += 1
                return best_index
            elif hi - lo == 1:
                return mid
            elif err > 0:
                hi = mid
            else:
                lo = mid
        return 0

    def between_mz(self, low, high):
        """Returns a slice of the arrays between ``low`` and ``high``
        m/z

        Parameters
        ----------
        low : float
            The lower bound m/z
        high : float
            The upper bound m/z

        Returns
        -------
        :class:`.RawDataArrays`
        """
        i = self.find_mz(low)
        j = self.find_mz(high) + 1
        if not (low <= self.mz[i] <= high):
            i += 1
        return self.__class__(self.mz[i:j], self.intensity[i:j])

    def __getitem__(self, i):
        if isinstance(i, int):
            return super(RawDataArrays, self).__getitem__(i)
        else:
            return self.arrays[i]


class ScanBase(object):
    '''Abstract base class for Scan-like objects
    '''

    def has_ion_mobility(self):
        '''Check whether this scan has drift time information associated with
        it.

        If this scan has been aggregated, it will only check the first scan in
        the aggregate.
        '''
        acq = self.acquisition_information
        if acq is None:
            return False
        scan_event = acq[0]
        return scan_event.has_ion_mobility()

    @property
    def drift_time(self):
        '''A convenience method to access the first
        scan event to retrieve its drift time.

        Returns
        -------
        float or None
        '''
        acq = self.acquisition_information
        if acq is None:
            return None
        scan_event = acq[0]
        return scan_event.drift_time

    @property
    def scan_id(self):
        '''An alias for :attr:`id`
        '''
        return self.id

    @property
    def tic(self):
        """A facade function for calculating the total ion current (TIC) of a spectrum.

        This exposes a facade object of type :class:`TICMethods` to take care of the different
        ways in which the TIC may be calculated.

        Returns
        -------
        :class:`TICMethods`

        Examples
        --------
        Just directly calling the `tic` attribute will use the most refined data source
        to calculate the TIC. This means that if the TIC is recalculated after refinement,
        the number may change.

        >>> from ms_deisotope.test.common import example_scan_bunch
        >>> bunch = example_scan_bunch()
        >>> bunch.precursor.tic()
        8886549.0
        >>> bunch.precursor.tic.raw()
        8886549.0

        The picked peaks can be used through :meth:`TICMethods.centroided`, which take
        priority over the raw signal when calling :meth:`tic` directly.
        >>> bunch.precursor.pick_peaks()
        >>> bunch.precursor.tic.centroided()
        8886548.890350103

        The deconvoluted peaks can be used through :meth:`TICMethods.deconvoluted`.
        >>> bunch.precursor.deconvolute(use_quick_charge=True)
        >>> bunch.precursor.tic.deconvoluted()
        8195619.241884331
        >>> bunch.precursor.tic()
        8195619.241884331

        """
        return TICMethods(self)

    @property
    def base_peak(self):
        """A facade function for calculating the base peak, the most abundant peak,
        of a spectrum.

        This exposes a facade object of type :class:`BasePeakMethods` to take care of
        the different ways in which the base peak may be calculated. The interface of
        this object is the same as the interface exposed by the :attr:`tic` attribute,
        but instead of returning a scalar float, it returns a :class:`~.PeakLike` object.

        Returns
        -------
        :class:`BasePeakMethods`

        See Also
        --------
        :attr:`tic`
        """
        return BasePeakMethods(self)

    def copy(self, deep=True):
        """Return a deep copy of the :class:`Scan` object
        wrapping the same reference data.

        Returns
        -------
        :class:`ScanBase`
        """
        return self.clone(deep)

    def __copy__(self):
        return self.clone()

    def __eq__(self, other):
        if other is None:
            return False
        if not isinstance(other, ScanBase):
            return False
        try:
            eq = (self.scan_id == other.scan_id) and (
                abs(self.scan_time - other.scan_time) < 1e-3) and (
                    self.index == other.index) and (
                        self.ms_level == other.ms_level)
            if not eq:
                return False
        except AttributeError:
            return False
        try:
            eq = self.arrays == other.arrays
            if not eq:
                return False
        except AttributeError:
            # ProcessedScan doesn't have an arrays attribute
            pass
        try:
            eq = self.peak_set == other.peak_set
            if not eq:
                return False
        except AttributeError:
            if ((self.peak_set is None and other.peak_set is not None) or (
                    self.peak_set is not None and other.peak_set is None)):
                pass
            else:
                return False

        try:
            eq = self.deconvoluted_peak_set == other.deconvoluted_peak_set
            if not eq:
                return False
        except AttributeError:
            if ((self.deconvoluted_peak_set is None and other.deconvoluted_peak_set is not None) or (
                    self.deconvoluted_peak_set is not None and other.deconvoluted_peak_set is None)):
                pass
            else:
                return False

        eq = self.precursor_information == other.precursor_information
        if not eq:
            return False
        eq = self.isolation_window == other.isolation_window
        if not eq:
            return False
        try:
            a = self.acquisition_information
            b = other.acquisition_information
            if a is not None and b is not None:
                eq = a == b
            else:
                eq = True
            if not eq:
                return False
        except AttributeError:
            pass
        try:
            a = self.activation
            b = other.activation
            if a is not None and b is not None:
                eq = a == b
            else:
                eq = True
            if not eq:
                return False
        except AttributeError:
            pass

        return True

    def __ne__(self, other):
        return not (self == other)

    def bind(self, source):
        '''Attach this object and its other referent members
        to ``source``, letting them load information.
        '''
        if self.precursor_information is not None:
            self.precursor_information.bind(source)
        return self

    def unbind(self):
        '''Detattch this object and its other referent members
        from their currently bound :attr:`source`.

        This may cause errors if more information is requested but is not
        cached, or if requesting another :class:`ScanBase` be loaded.
        '''
        if self.precursor_information is not None:
            self.precursor_information.unbind()
        return self


class PrecursorInformation(object):
    """Store information relating a tandem MS scan to its precursor MS scan.

    .. note::
        The attributes prefixed with `extracted_` refer to the quantities estimated
        from the data, while those unprefixed are the values read directly from the
        data source. These values regularly do not agree. When available, the extracted
        values should be more accurate.

    Attributes
    ----------
    charge : int
        The charge reported in the source metadata
    defaulted : bool
        Whether the information in the extracted fields reflects empirical
        information or fell back on the vendor-reported values.
    extracted_charge : int
        The charge estimated from the source data
    extracted_intensity : float
        The sum of the peak heights of the extracted isotopic pattern
    extracted_neutral_mass : float
        The monoisotopic neutral mass estimated from the source data
    extracted_peak : :class:`.DeconvolutedPeak`
        The deconvoluted peak summarizing the precursor ion
    intensity : float
        The abundance reported in the source metadata
    mz : float
        The m/z reported in the source metadata
    orphan : bool
        Whether there was an isotopic pattern to extract in the precursor scan. Usually
        paired with `defaulted`
    peak : :class:`.FittedPeak`
        The peak nearest :attr:`mz`, and the starting point for estimating information
        about the precursor ion
    precursor_scan_id : str
        The id string for the precursor scan
    source : :class:`ScanIterator`
        Any object implementing the :class:`ScanIterator` interface to be used to look up
        the precursor scan with :attr:`precursor_scan_id`
    """

    def __init__(self, mz, intensity, charge, precursor_scan_id=None, source=None,
                 extracted_neutral_mass=0, extracted_charge=0, extracted_intensity=0,
                 peak=None, extracted_peak=None, defaulted=False, orphan=False,
                 product_scan_id=None, annotations=None, coisolation=None):
        try:
            charge = int(charge)
        except Exception:
            pass
        try:
            extracted_charge = int(extracted_charge)
        except Exception:
            pass
        if not annotations:
            annotations = {}
        if not coisolation:
            coisolation = []

        self.mz = mz
        self.intensity = intensity
        self.charge = charge

        self.precursor_scan_id = precursor_scan_id
        self.source = source

        self.extracted_neutral_mass = extracted_neutral_mass
        self.extracted_charge = extracted_charge
        self.extracted_intensity = extracted_intensity

        self.peak = peak
        self.extracted_peak = extracted_peak
        self.defaulted = defaulted
        self.orphan = orphan
        self.product_scan_id = product_scan_id

        self.annotations = annotations
        self.coisolation = coisolation

    def __repr__(self):
        return "PrecursorInformation(mz=%0.4f/%0.4f, intensity=%0.4f/%0.4f, charge=%r/%r, scan_id=%r)" % (
            self.mz,
            self.extracted_mz if self.extracted_neutral_mass != 0. else 0.,
            self.intensity or 0., self.extracted_intensity or 0., self.charge,
            self.extracted_charge or 0., self.precursor_scan_id)

    def __reduce__(self):
        return self.__class__, (0, 0, 0), self.__getstate__()

    def __getstate__(self):
        # explicitly do not propagate :attr:`source` when serializing.
        return (self.mz, self.intensity, self.charge, self.precursor_scan_id, None, self.extracted_neutral_mass,
                self.extracted_charge, self.extracted_intensity, self.peak, self.extracted_peak,
                self.defaulted, self.orphan, self.product_scan_id, self.annotations, self.coisolation)

    def __setstate__(self, state):
        (self.mz, self.intensity, self.charge, self.precursor_scan_id, self.source, self.extracted_neutral_mass,
         self.extracted_charge, self.extracted_intensity, self.peak, self.extracted_peak,
         self.defaulted, self.orphan, self.product_scan_id) = state[:13]
        if len(state) > 13:
            self.annotations = state[13]
        if len(state) > 14:
            self.coisolation = list(state[14])

    def __eq__(self, other):
        if other is None:
            return False
        eq = self.precursor_scan_id == other.precursor_scan_id
        if not eq:
            return False
        eq = self.product_scan_id == other.product_scan_id
        if not eq:
            return False
        self_fit = self.extracted_neutral_mass != 0
        other_fit = other.extracted_neutral_mass != 0
        self_mass = self.extracted_mz if self_fit else self.mz
        other_mass = other.extracted_mz if other_fit else other.mz
        eq = np.isclose(self_mass, other_mass)
        if not eq:
            return False
        self_charge = self.extracted_charge if self_fit else self.charge
        other_charge = other.extracted_charge if other_fit else other.charge
        eq = self_charge == other_charge
        if not eq:
            return False
        return True

    def __ne__(self, other):
        return not (self == other)

    def bind(self, source):
        '''Attach this object and its other referent members
        to ``source``, letting them load information.
        '''
        self.source = source
        return self

    def unbind(self):
        '''Detattch this object the currently bound :attr:`source`.
        '''
        self.source = None

    def extract(self, peak, override_charge=None):
        '''Populate the extracted attributes of this object from the attributes
        of a :class:`~.DeconvolutedPeak` instance.

        Parameters
        ----------
        peak: :class:`~.DeconvolutedPeak`
            The peak to copy attributes from
        override_charge: :class:`int`, optional
            If provided, this charge will be used instead of the charge of ``peak``
        '''
        self.extracted_neutral_mass = peak.neutral_mass
        self.extracted_charge = int(
            peak.charge) if override_charge is None else override_charge
        self.extracted_intensity = peak.intensity
        self.extracted_peak = peak

    def default(self, orphan=False):
        '''Populate the extracted attributes of this object from the matching
        original attributes.

        This usually reflects a failure to find an acceptable deconvolution solution,
        and may indicate that there was no peak at the specified location when ``orphan``
        is :const:`True`

        Parameters
        ----------
        orphan: :class:`bool`
            Whether or not to set :attr:`orphan` to :const:`True`, indicating no peak was
            found near :attr:`mz`.
        '''
        if self.charge == ChargeNotProvided:
            warnings.warn(
                "A precursor has been defaulted with an unknown charge state.")
            self.extracted_charge = ChargeNotProvided
            self.extracted_neutral_mass = neutral_mass(
                self.mz, DEFAULT_CHARGE_WHEN_NOT_RESOLVED)
            self.extracted_intensity = self.intensity
            self.defaulted = True
        else:
            self.extracted_charge = int(self.charge)
            self.extracted_neutral_mass = self.neutral_mass
            self.extracted_intensity = self.intensity
            self.defaulted = True
        if orphan:
            self.orphan = True

    @property
    def neutral_mass(self):
        """Calculate the neutral mass of the precursor from the given m/z and charge.

        Returns
        -------
        float
        """
        if self.charge == ChargeNotProvided:
            warnings.warn(
                "A precursor with an unknown charge state was used to compute a neutral mass.")
            return neutral_mass(self.mz, DEFAULT_CHARGE_WHEN_NOT_RESOLVED)
        return neutral_mass(self.mz, self.charge)

    @property
    def extracted_mz(self):
        """Recalculate the m/z of the precursor from the fitted neutral mass and charge

        Returns
        -------
        float
        """
        if self.extracted_charge == ChargeNotProvided or (
                self.extracted_charge == 0 and self.charge == ChargeNotProvided):
            warnings.warn(
                "A precursor with an unknown charge state was used to compute a m/z.")
            return mass_charge_ratio(self.mz, DEFAULT_CHARGE_WHEN_NOT_RESOLVED)
        return mass_charge_ratio(self.extracted_neutral_mass, self.extracted_charge)

    @property
    def precursor(self):
        """The scan in which the precursor ion was isolated.

        Returns
        -------
        :class:`ScanBase`
        """
        if self.precursor_scan_id is None:
            return None
        return self.source.get_scan_by_id(self.precursor_scan_id)

    @property
    def product(self):
        """The scan in which the precursor ion was fragmented and daughter ions were observed.

        Returns
        -------
        :class:`ScanBase`
        """
        if self.product_scan_id is None:
            return None
        return self.source.get_scan_by_id(self.product_scan_id)

    def copy(self):
        """Make a shallow copy of this object.

        Returns
        -------
        :class:`PrecursorInformation`
        """
        dup = self.__class__(
            self.mz, self.intensity, self.charge, self.precursor_scan_id, self.source,
            self.extracted_neutral_mass, self.extracted_charge, self.extracted_intensity,
            self.peak, self.extracted_peak, self.defaulted, self.orphan,
            self.product_scan_id, self.annotations, self.coisolation)
        return dup

    def clone(self):
        """Make a shallow copy of this object.

        .. note::

            This is an alias of :meth:`copy`

        Returns
        -------
        :class:`PrecursorInformation`
        """
        return self.copy()

    def correct_mz(self, error_tolerance=2e-5, enforce_isolation_window=False):
        """Find the peak nearest to :attr:`mz` in :attr:`precursor` and
        update :attr:`mz` from it.

        .. note::
            The peak selected may still not be the monoisotopic peak. This requires
            a deconvolution procedure.

        Parameters
        ----------
        error_tolerance: float, optional
            The error tolerance in PPM to use when searching for the nearest peak (the default is 2e-5).
        enforce_isolation_window: bool, optional
            Whether or not to force the specified m/z. Defaults to :const:`False`.
        """
        if self.precursor_scan_id is None:
            return
        precursor_scan = self.precursor
        if precursor_scan is None:
            return
        if precursor_scan.peak_set is None:
            precursor_scan.pick_peaks()
        peaks = precursor_scan.peak_set
        peak = peaks.has_peak(self.mz, error_tolerance)
        if peak is not None:
            self.mz = peak.mz
        if enforce_isolation_window and self.product_scan_id is not None:
            product_scan = self.product
            if product_scan is not None:
                isolation_window = product_scan.isolation_window
            else:
                isolation_window = None
            if isolation_window is not None and not isolation_window.is_empty():
                if not isolation_window.spans(self.mz):
                    region = peaks.between(
                        isolation_window.lower_bound, isolation_window.upper_bound)
                    if region:
                        peak = max(region, key=lambda x: x.intensity)
                        self.mz = peak.mz

    def find_monoisotopic_peak(self, trust_charge_state=True, precursor_scan=None, **kwargs):
        """Find the monoisotopic peak for this precursor.

        This convenience method carries out a simplified procedure for finding the
        precursor ion's monoisotpic peak and charge state in the precursor scan. It
        follows steps similar to those found in the :class:`~.ScanProcessor` pipeline,
        but is not as flexible or complete.

        .. note::

            For a full deconvolution result, please use :class:`~.ScanProcessor`, which
            carries out a more complete error checking procedure.


        Parameters
        ----------
        trust_charge_state: bool, optional
            Whether or not to trust the original precursor charge state, which may be based
            upon information not available in the examined mass spectrum.
        precursor_scan: :class:`~.ScanBase`, optional
            The spectrum to look for the precursor peak in. If not provided,
            :attr:`precursor` will be used.

        Returns
        -------
        :class:`float`:
            The updated m/z of the precursor ion's monoisotopic peak.
        :class:`bool`:
            Whether or not the deconvolution procedure was able to run successfully.

        """
        if precursor_scan is None:
            if self.precursor_scan_id is None:
                return False
            precursor_scan = self.precursor
            if precursor_scan is None:
                return False
        if precursor_scan.peak_set is None:
            precursor_scan.pick_peaks()
        charge_range = kwargs.get("charge_range", (1, 8))
        if precursor_scan.polarity < 0 and max(charge_range) > 0:
            charge_range = tuple(c * precursor_scan.polarity for c in charge_range)
        kwargs['charge_range'] = charge_range
        ref_peak = precursor_scan.has_peak(self.mz, 2e-5)
        # No experimental peak found, so mark that this precursor is an orphan and default it
        if ref_peak is None:
            self.default(orphan=True)
            return self.extracted_mz, False

        priority_target = [ref_peak]
        peaks = precursor_scan.peak_set.between(self.mz - 3, self.mz + 6)
        peaks = peaks.clone()
        peaks.reindex()
        result = deconvolute_peaks(peaks, priority_list=priority_target, **kwargs)
        _, priority_results = result
        peak = priority_results[0]
        # No deconvoluted peak found, so mark that this precursor is an orphan and default it
        if peak is None:
            self.default(orphan=True)
            return self.extracted_mz, False
        # A peak was found, we don't know the expected charge state, so accept it
        # and extract the updated peak fit
        elif self.charge == ChargeNotProvided:
            self.extract(peak)
            return self.extracted_mz, True
        # A peak was found, but it doesn't match the trusted charge state, so reject
        # it and default this precursor
        if trust_charge_state and self.charge != peak.charge:
            self.default()
            return self.extracted_mz, False
        # The returned peak matches the expected charge state, so accept it and
        # extract the updated peak fit
        self.extract(peak)
        return self.extracted_mz, True


class TICMethods(object):
    """A helper class that will figure out the most refined signal source to
    calculate the total ion current from.
    """
    def __init__(self, scan):
        self.scan = scan

    def _peak_set_tic(self, peaks):
        total = 0
        for peak in peaks:
            total += peak.intensity
        return total

    def _peak_sequence_tic(self, peaks):
        total = 0
        for peak in peaks:
            total += peak.intensity
        return total

    def _simple_tic(self, points):
        return sum(points)

    def _tic_raw_data_arrays(self, arrays):
        return arrays.intensity.sum()

    def __call__(self):
        return self._guess()

    def _guess(self):
        try:
            return self.deconvoluted()
        except (AttributeError, TypeError):
            pass
        try:
            return self.centroided()
        except (AttributeError, TypeError):
            pass
        try:
            return self.raw()
        except (AttributeError, TypeError):
            pass

        points = list(self.scan)
        if points:
            if isinstance(points[0], PeakLike):
                return self._peak_sequence_tic(points)
            elif isinstance(points[0], Number):
                return self._simple_tic(points)
            else:
                raise TypeError(
                    "Cannot determine how to calculate a TIC from %r of type %r" % (
                        self.scan, type(self.scan)))
        else:
            raise TypeError(
                "Cannot determine how to calculate a TIC from %r of type %r" % (
                    self.scan, type(self.scan)))

    def raw(self):
        """Calculate the TIC from the raw intensity signal of the spectrum with no processing.

        Returns
        -------
        float
        """
        return self._tic_raw_data_arrays(self.scan.arrays)

    def centroided(self):
        """Calculate the TIC from the picked peak list of the spectrum.

        Returns
        -------
        float
        """
        return self._peak_set_tic(self.scan.peak_set)

    def deconvoluted(self):
        """Calculate the TIC from the deconvoluted peak list of the spectrum.

        Returns
        -------
        float
        """
        return self._peak_set_tic(self.scan.deconvoluted_peak_set)


class BasePeakMethods(object):
    """A helper class that will figure out the most refined signal source to
    calculate the base peak from.
    """

    base_peak_t = namedtuple("BasePeak", ("mz", "intensity"))

    def __init__(self, scan):
        self.scan = scan

    def _peak_set_bp(self, peaks):
        if not peaks:
            return None
        peak = max(peaks, key=lambda x: x.intensity)
        return peak

    def _peak_sequence_bp(self, peaks):
        if not peaks:
            return None
        peak = max(peaks, key=lambda x: x.intensity)
        return peak

    def _bp_raw_data_arrays(self, arrays):
        i = np.argmax(arrays.intensity)
        return self.base_peak_t(arrays.mz[i], arrays.intensity[i])

    def __call__(self):
        return self._guess()

    def _guess(self):
        try:
            return self.deconvoluted()
        except (AttributeError, TypeError):
            pass
        try:
            return self.centroided()
        except (AttributeError, TypeError):
            pass
        try:
            return self.raw()
        except (AttributeError, TypeError):
            pass

        points = list(self.scan)
        if points:
            if isinstance(points[0], PeakLike):
                return self._peak_sequence_bp(points)
            raise TypeError(
                "Cannot determine how to calculate a base peak from %r of type %r" % (
                    self.scan, type(self.scan)))
        else:
            raise TypeError(
                "Cannot determine how to calculate a base peak from %r of type %r" % (
                    self.scan, type(self.scan)))

    def raw(self):
        """Calculate the TIC from the raw intensity signal of the spectrum with no processing.

        Returns
        -------
        float
        """
        return self._bp_raw_data_arrays(self.scan.arrays)

    def centroided(self):
        """Calculate the TIC from the picked peak list of the spectrum.

        Returns
        -------
        float
        """
        return self._peak_set_bp(self.scan.peak_set)

    def deconvoluted(self):
        """Calculate the TIC from the deconvoluted peak list of the spectrum.

        Returns
        -------
        float
        """
        return self._peak_set_bp(self.scan.deconvoluted_peak_set)

try:
    from ms_deisotope._c.utils import _peak_sequence_tic, _peak_sequence_bp
    TICMethods._peak_set_tic = _peak_sequence_tic
    TICMethods._peak_sequence_tic = _peak_sequence_tic
    BasePeakMethods._peak_set_bp = _peak_sequence_bp
    BasePeakMethods._peak_sequence_bp = _peak_sequence_bp
except ImportError:
    pass
