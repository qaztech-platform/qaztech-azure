import pathlib, torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
H = pathlib.Path.home() / "azure-engineer"
OUT = H / "QAZTECH-Azure-Engineer-8B"
print("загрузка Qwen3-8B...", flush=True)
m = AutoModelForCausalLM.from_pretrained("Qwen/Qwen3-8B", torch_dtype=torch.bfloat16)
print("слияние с адаптером...", flush=True)
m = PeftModel.from_pretrained(m, str(H / "adapter_final")).merge_and_unload()
m.save_pretrained(OUT, safe_serialization=True, max_shard_size="5GB")
AutoTokenizer.from_pretrained("Qwen/Qwen3-8B").save_pretrained(OUT)
print("СЛИЯНИЕ ГОТОВО:", OUT, flush=True)
