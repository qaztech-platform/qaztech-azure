import os, torch
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM
from trl import SFTTrainer, SFTConfig
BASE = os.environ.get("BASE", "Qwen/Qwen3-8B")
D = os.path.expanduser(os.environ.get("DATA", "~/data"))
EPOCHS = float(os.environ.get("EPOCHS", "2")); MAXS = int(os.environ.get("MAX_STEPS", "-1"))
pc = lambda ex: {"prompt": ex["messages"][:2], "completion": ex["messages"][2:]}
ld = lambda f: load_dataset("json", data_files=f"{D}/{f}", split="train").map(pc, remove_columns=["messages"])
tr, ev = ld("train_clean.jsonl"), ld("eval.jsonl")
model = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.bfloat16)
cfg = SFTConfig(output_dir="out", num_train_epochs=EPOCHS, max_steps=MAXS, per_device_train_batch_size=int(os.environ.get("BS","2")), per_device_eval_batch_size=int(os.environ.get("EVAL_BS","1")),
    gradient_accumulation_steps=int(os.environ.get("ACCUM","2")), learning_rate=1e-4, lr_scheduler_type="cosine", warmup_ratio=0.03,
    bf16=True, logging_steps=10, eval_strategy="steps", eval_steps=50, save_strategy="steps",
    save_steps=100, save_total_limit=2, max_length=int(os.environ.get("MAXLEN", "2048")), gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False}, ddp_find_unused_parameters=False, load_best_model_at_end=True, metric_for_best_model="eval_loss", greater_is_better=False, report_to="none")
lora = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, task_type="CAUSAL_LM",
    target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"])
tr_ = SFTTrainer(model=model, args=cfg, train_dataset=tr, eval_dataset=ev, peft_config=lora)
tr_.train(); tr_.save_model("out/final"); print("АДАПТЕР СОХРАНЁН: out/final")
if int(os.environ.get("RANK", "0")) != 0: raise SystemExit(0)
try:
    tok = tr_.processing_class; m = tr_.model; m.eval()
    for ex in ev.select(range(4)):
        ids = tok.apply_chat_template(ex["prompt"], add_generation_prompt=True, enable_thinking=False, return_tensors="pt", return_dict=True).to(m.device)
        out = m.generate(**ids, max_new_tokens=250, do_sample=False)
        print("----\nВОПРОС:", ex["prompt"][-1]["content"][:150], "\nОТВЕТ:", tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)[:500])
except Exception as e: print("ПРОБНЫЕ ОТВЕТЫ НЕ ПОЛУЧИЛИСЬ:", repr(e))
