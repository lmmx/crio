import time
import crio

start = time.time()

print("Program start:", start)

with crio.checkpoint():
    print(">> Inside checkpoint: starting expensive setup")

    import time as _t

    _t.sleep(5)  # simulate heavy work

    setup_time = time.time()
    print(">> Setup finished at:", setup_time)

    data = {
        "setup_completed_at": setup_time,
    }

print("After checkpoint block")

end = time.time()

print("Data:", data)
print("Total runtime:", round(end - start, 2), "seconds")
