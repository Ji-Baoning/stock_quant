import tushare as ts

# 初始化pro接口，它会自动从环境变量中读取你的Token
pro = ts.pro_api()

# 获取指定交易日（例如2026年9月4日）的全市场股票日线数据
df = pro.daily(trade_date='20260904')

# 查看数据
print(df.head())