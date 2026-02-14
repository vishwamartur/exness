from gluonts.time_feature import get_lags_for_frequency

lags_seq = ["Q", "M", "W", "D", "H", "T", "S"]
lag_indices = []
for freq in lags_seq:
    lag_indices.extend(
        get_lags_for_frequency(freq_str=freq, num_default_lags=1)
    )

unique_lags = sorted(set(lag_indices))
# LagLlama subtracts 1 to make them 0-indexed indices for embedding lookups? 
# No, it uses them as lag values.
print(f"Total unique lags: {len(unique_lags)}")
print(f"Lags: {unique_lags}")
