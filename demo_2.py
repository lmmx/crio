import crio


def main():
    print("Starting demo")
    with crio.checkpoint():
        print("Inside checkpoint context")
        # Do something here
        x = 42
        print(f"x = {x}")

    print("Checkpoint completed successfully")


if __name__ == "__main__":
    main()
