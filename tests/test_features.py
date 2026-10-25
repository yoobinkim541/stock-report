

def test_cci_vectorized_matches_rolling_apply_reference():
    """CCI MAD 벡터화 — rolling.apply(lambda) 기준 구현과 동일(NaN 포함)."""
    import numpy as np
    import pandas as pd
    from ml import features as F
    rng = np.random.default_rng(0)
    close = pd.Series(100 + rng.standard_normal(300).cumsum())
    close.iloc[50] = np.nan
    df = pd.DataFrame({"high": close + 1, "low": close - 1})
    typical = (df["high"] + df["low"] + close) / 3
    ref_mad = typical.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    ref = (typical - typical.rolling(20).mean()) / (0.015 * ref_mad.replace(0, np.nan))
    got = F.cci(close, df, period=20)
    assert np.allclose(ref.to_numpy(), got.to_numpy(), equal_nan=True)
    assert F.cci(close.iloc[:5], df.iloc[:5]).isna().all()
