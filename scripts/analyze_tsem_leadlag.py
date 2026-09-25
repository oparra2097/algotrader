"""
TSEM lead-lag / factor analysis vs NVDA, ^GSPC, ^DJI.
Run locally: pip install yfinance pandas numpy statsmodels && python tsem_analysis.py
"""
import numpy as np
import pandas as pd
import yfinance as yf
import statsmodels.api as sm
from statsmodels.stats.outliers_influence import variance_inflation_factor

TICKERS = ["TSEM", "NVDA", "^GSPC", "^DJI"]
START = "2023-09-25"
END   = "2026-09-26"     # yfinance end is exclusive

# ---------- fetch ----------
raw = yf.download(TICKERS, start=START, end=END, auto_adjust=True,
                  progress=False, group_by="ticker")
closes = pd.DataFrame({t: raw[t]["Close"] for t in TICKERS}).dropna()
closes.columns = ["TSEM", "NVDA", "SPX", "DJI"]
print(f"Rows: {len(closes)}  ({closes.index.min().date()} -> {closes.index.max().date()})")

rets = np.log(closes / closes.shift(1)).dropna()
n = len(rets)

# ---------- (2a) correlation matrix ----------
print("\n=== Correlation matrix (daily log returns) ===")
print(rets.corr().round(4))

# ---------- (2b) rolling 60d corr ----------
roll = pd.DataFrame({
    "TSEM~NVDA": rets["TSEM"].rolling(60).corr(rets["NVDA"]),
    "TSEM~SPX":  rets["TSEM"].rolling(60).corr(rets["SPX"]),
    "TSEM~DJI":  rets["TSEM"].rolling(60).corr(rets["DJI"]),
}).dropna()
print("\n=== Rolling 60d corr summary ===")
print(roll.describe().round(3).loc[["mean","std","min","50%","max"]])

# ---------- (2c) full OLS ----------
X = sm.add_constant(rets[["NVDA","SPX","DJI"]])
m = sm.OLS(rets["TSEM"], X).fit()
print("\n=== OLS: TSEM ~ NVDA + SPX + DJI ===")
print(m.summary().tables[1])
print(f"R^2 = {m.rsquared:.4f}   adj R^2 = {m.rsquared_adj:.4f}   n = {int(m.nobs)}")
print("VIFs:", {c: round(variance_inflation_factor(X.values, i+1),2)
                 for i,c in enumerate(["NVDA","SPX","DJI"])})

# ---------- (2d) reduced OLS ----------
X2 = sm.add_constant(rets[["NVDA","SPX"]])
m2 = sm.OLS(rets["TSEM"], X2).fit()
print("\n=== OLS: TSEM ~ NVDA + SPX ===")
print(m2.summary().tables[1])
print(f"R^2 = {m2.rsquared:.4f}")

for c in ["NVDA","SPX","DJI"]:
    mi = sm.OLS(rets["TSEM"], sm.add_constant(rets[[c]])).fit()
    print(f"  Univariate TSEM~{c}: beta={mi.params[c]:.3f} t={mi.tvalues[c]:.2f} R^2={mi.rsquared:.4f}")

# ---------- (2e) vol ----------
dv = rets["TSEM"].std()
av = dv*np.sqrt(252)
print(f"\nTSEM daily stdev: {dv*100:.2f}%   annualized: {av*100:.2f}%")
print(f"TSEM mean |ret|:  {rets['TSEM'].abs().mean()*100:.2f}%   median: {rets['TSEM'].abs().median()*100:.2f}%")
for c in ["NVDA","SPX","DJI"]:
    print(f"  {c} ann vol: {rets[c].std()*np.sqrt(252)*100:.2f}%")

# ---------- (3) lead-lag ----------
def rt(a, b):
    a, b = a.align(b, join="inner"); m = a.notna()&b.notna(); a,b = a[m],b[m]
    r = np.corrcoef(a,b)[0,1]; nn=len(a)
    t = r*np.sqrt((nn-2)/(1-r**2)) if abs(r)<1 else float('inf')
    return r,t,nn

print(f"\n=== Lead-lag (Pearson r) — TSEM[t] vs X[t-lag] ===")
for lag in [1,2,3,5]:
    for c in ["NVDA","SPX","DJI"]:
        r,t,nn = rt(rets["TSEM"], rets[c].shift(lag))
        print(f"  TSEM[t] vs {c}[t-{lag}]: r={r:+.4f}  t={t:+.2f}  n={nn}")

print("\n=== Reverse: X[t] vs TSEM[t-lag] ===")
for lag in [1,2]:
    for c in ["NVDA","SPX","DJI"]:
        r,t,nn = rt(rets[c], rets["TSEM"].shift(lag))
        print(f"  {c}[t] vs TSEM[t-{lag}]: r={r:+.4f}  t={t:+.2f}")

# ---------- (3b) OLS on lags ----------
lag_df = pd.DataFrame({
    "TSEM_l1": rets["TSEM"].shift(1),
    "NVDA_l1": rets["NVDA"].shift(1),
    "SPX_l1":  rets["SPX"].shift(1),
    "DJI_l1":  rets["DJI"].shift(1),
    "TSEM":    rets["TSEM"],
}).dropna()
X3 = sm.add_constant(lag_df[["NVDA_l1","SPX_l1","DJI_l1"]])
m3 = sm.OLS(lag_df["TSEM"], X3).fit()
print("\n=== OLS: TSEM[t] ~ lag1(NVDA,SPX,DJI) ===")
print(m3.summary().tables[1])
print(f"R^2 = {m3.rsquared:.4f}")

X4 = sm.add_constant(lag_df[["TSEM_l1","NVDA_l1","SPX_l1"]])
m4 = sm.OLS(lag_df["TSEM"], X4).fit()
print("\n=== OLS: TSEM[t] ~ TSEM_l1 + NVDA_l1 + SPX_l1 ===")
print(m4.summary().tables[1])
print(f"R^2 = {m4.rsquared:.4f}")

# ---------- (4) options premium sanity ----------
S = closes["TSEM"].iloc[-1]
print(f"\nTSEM last close: ${S:.2f}")
for days in [1,5,21]:
    straddle = S*av*np.sqrt(days/252)*np.sqrt(2/np.pi)   # Brenner–Subrahmanyam ATM
    print(f"  T={days:2d}d  ATM straddle ~ ${straddle:.2f}  break-even move = {straddle/S*100:.2f}%")

be1 = av*np.sqrt(1/252)*np.sqrt(2/np.pi)
frac = (rets['TSEM'].abs() > be1).mean()
be1iv = 1.25*be1
fraciv = (rets['TSEM'].abs() > be1iv).mean()
print(f"  1d BE (IV=RV):    {be1*100:.2f}%   hit-rate {frac*100:.1f}%")
print(f"  1d BE (IV=1.25RV): {be1iv*100:.2f}%   hit-rate {fraciv*100:.1f}%")
