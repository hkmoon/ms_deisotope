import unittest

from ms_deisotope.data_source import MzXMLLoader
from ms_deisotope.test.common import datafile
from ms_deisotope.data_source import infer_type


class TestMzXMLLoaderScanBehavior(unittest.TestCase):
    path = datafile("microscans.mzXML")

    @property
    def reader(self):
        return infer_type.MSFileLoader(self.path)

    @property
    def first_scan(self):
        return self.reader.next().precursor

    def test_id(self):
        loader = self.reader
        scan = next(loader).precursor
        self.assertEqual(scan.id, "210")
        scan = loader.get_scan_by_id("210")
        self.assertEqual(scan.id, "210")

    def test_polarity(self):
        self.assertEqual(self.first_scan.polarity, 1)

    def test_index(self):
        self.assertEqual(self.first_scan.polarity, 1)

    def test_arrays(self):
        self.assertEqual(len(self.first_scan.arrays), 2)

    def test_precursor_info(self):
        self.assertEqual(self.first_scan.precursor_information, None)

    def test_file_description(self):
        file_info = self.reader.file_description()
        source_file = file_info.source_files[0]
        assert source_file.name == "AGP_tryptic_300ng_3microscans_glycoproteomics_nCE_27-35.raw"
        assert "location" not in source_file.parameters


if __name__ == '__main__':
    unittest.main()
