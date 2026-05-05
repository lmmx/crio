import crio

with crio.checkpoint():
    import time

    import mvdef  # torch

    time.sleep(2)
    print("Slept")

# print(torch.cuda.is_available())
print(mvdef)
