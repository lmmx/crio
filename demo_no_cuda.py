import crio

with crio.checkpoint():
    import json
    import collections
    import decimal
    data = {"restored": True}

print(data)
print(decimal.Decimal("3.14"))