import crio

with crio.checkpoint():
    import mvdef  # torch

# print(torch.cuda.is_available())
print(mvdef)
