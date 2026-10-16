import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal, assert_series_equal
from ml.entry_analyzer import _calculation_data, _compute_ticker_features

class NoDeepcopy:
    def __deepcopy__(self, memo):
        raise AssertionError("calculation must not deepcopy provenance")

def test_calculation_copy_preserves_original_metadata_and_values():
    for source in (pd.Series([1., 2.], name="Close"),
                   pd.DataFrame({"Close": [1., 2.], "Volume": [1, 2]})):
        expected = type(source)(source, copy=True)
        marker = NoDeepcopy()
        source.attrs["data_snapshot"] = marker
        result = _calculation_data(source)
        assert result.attrs == {}
        assert source.attrs["data_snapshot"] is marker
        if isinstance(source, pd.Series):
            assert_series_equal(result, expected)
            result.iloc[0] = 99
            assert source.iloc[0] == 1
        else:
            assert_frame_equal(result, expected)
            result.iloc[0, 0] = 99
            assert source.to_numpy()[0, 0] == 1

def test_features_ignore_snapshot_without_changing_results():
    index = pd.date_range("2020-01-01", periods=400)
    price = pd.Series(100 + np.arange(400) * .03 + np.sin(np.arange(400)), index=index)
    vix = pd.Series(20 + np.cos(np.arange(400)), index=index)
    expected = _compute_ticker_features(price, vix)
    marker = NoDeepcopy()
    price.attrs["data_snapshot"] = marker
    vix.attrs["data_snapshot"] = marker
    actual = _compute_ticker_features(price, vix)
    assert_frame_equal(actual, expected)
    assert price.attrs["data_snapshot"] is marker
    assert vix.attrs["data_snapshot"] is marker
