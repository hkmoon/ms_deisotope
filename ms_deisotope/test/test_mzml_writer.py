import unittest
import os
import tempfile

from ms_deisotope.data_source import MzMLLoader
from ms_deisotope.test.common import datafile
from ms_deisotope.data_source import infer_type

from ms_deisotope.output.mzml import MzMLScanSerializer, ProcessedMzMLDeserializer


class TestMzMLScanSerializer(unittest.TestCase):
    source_data_path = datafile("three_test_scans.mzML")

    def test_writer(self):
        source_reader = MzMLLoader(self.source_data_path)
        fd, name = tempfile.mkstemp()
        with open(name, 'wb') as fh:
            writer = MzMLScanSerializer(fh, n_spectra=len(source_reader.index), deconvoluted=True)
            description = source_reader.file_description()
            wrote_spectrum_type = False
            for key in description.contents:
                if 'profile spectrum' in key:
                    key = {"centroid spectrum": ''}
                if 'centroid spectrum' in key:
                    if wrote_spectrum_type:
                        continue
                else:
                    wrote_spectrum_type = True
                writer.add_file_contents(key)
            # source_file_list_container = description.get('sourceFileList', {'sourceFile': []})
            for source_file in description.source_files:
                writer.add_source_file(source_file)

            instrument_configs = source_reader.instrument_configuration()
            for config in instrument_configs:
                writer.add_instrument_configuration(config)
            bunch = next(source_reader)
            bunch.precursor.pick_peaks()
            bunch.precursor.deconvolute()
            for product in bunch.products:
                product.pick_peaks()
                product.deconvolute()
            writer.save(bunch)
            writer.complete()
        source_reader.reset()
        processed_reader = ProcessedMzMLDeserializer(name)
        for a, b in zip(source_reader.instrument_configuration(), processed_reader.instrument_configuration()):
            assert a.analyzers == b.analyzers
        for a, b in zip(source_reader, processed_reader):
            assert a.precursor.id == b.precursor.id
            for an, bn in zip(a.products, b.products):
                assert an.id == bn.id
                assert abs(an.precursor_information.neutral_mass - bn.precursor_information.neutral_mass) < 1e-6
        processed_reader.close()
        try:
            os.remove(processed_reader.source_file)
            os.remove(processed_reader._index_file_name)
        except OSError:
            pass


if __name__ == '__main__':
    unittest.main()