import crio

with crio.checkpoint():
    import collections
    import decimal
    import json

    data = {"restored": True}

print(data)
print(decimal.Decimal("3.14"))
