import crio

with crio.checkpoint():
    print("Inside checkpoint context")
    # Do something here
    print("SETTING x = 42")
    x = 42
    print("IMPORT DATETIME.DATETIME")
    from datetime import datetime

    print("IMPORT TORCH, TRANSFORMERS, PYDANTIC")
    t1 = datetime.now()
    from pydantic import BaseModel, TypeAdapter, create_model
    from torch import cuda
    from transformers import AutoModel, AutoTokenizer

    t2 = datetime.now()
    print(f"IMPORTS TOOK {(t2-t1).seconds} SECONDS")

x
print("Checkpoint completed successfully")
cuda.is_available()
datetime.now().isoformat()
AutoModel
create_model("DataModel")
