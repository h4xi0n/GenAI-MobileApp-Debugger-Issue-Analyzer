from transformers import pipeline

generator = pipeline("text-generation", model="gpt2")

prompt = "In a world ruled by robots,"

print("=" * 60)
print(f"PROMPT: {prompt}")
print("=" * 60)

# 1. Greedy Search (Default)
print("\n--- 1. Greedy Search (do_sample=False) ---")
out_greedy = generator(
    prompt,
    max_new_tokens=30,
    do_sample=False,
    pad_token_id=generator.tokenizer.eos_token_id
)
print(out_greedy[0]["generated_text"])

# 2. Temperature (Low vs High Randomness)
print("\n--- 2. Low Temperature (temperature=0.2, focused) ---")
out_low_temp = generator(
    prompt,
    max_new_tokens=30,
    do_sample=True,
    temperature=0.2,
    pad_token_id=generator.tokenizer.eos_token_id
)
print(out_low_temp[0]["generated_text"])

print("\n--- 3. High Temperature (temperature=1.2, creative) ---")
out_high_temp = generator(
    prompt,
    max_new_tokens=30,
    do_sample=True,
    temperature=1.2,
    pad_token_id=generator.tokenizer.eos_token_id
)
print(out_high_temp[0]["generated_text"])

# 4. Top-K & Top-P (Nucleus Sampling)
print("\n--- 4. Top-K (top_k=50) & Top-P (top_p=0.9) ---")
out_top_kp = generator(
    prompt,
    max_new_tokens=30,
    do_sample=True,
    top_k=50,
    top_p=0.9,
    pad_token_id=generator.tokenizer.eos_token_id
)
print(out_top_kp[0]["generated_text"])

# 5. Multiple Sequences (num_return_sequences=2)
print("\n--- 5. Multiple Variations (num_return_sequences=2) ---")
out_multi = generator(
    prompt,
    max_new_tokens=25,
    do_sample=True,
    temperature=0.7,
    num_return_sequences=2,
    pad_token_id=generator.tokenizer.eos_token_id
)
for i, res in enumerate(out_multi, 1):
    print(f"Variation {i}: {res['generated_text']}\n")

# 6. Repetition Penalty & No Repeat N-Gram
print("\n--- 6. Repetition Penalty (repetition_penalty=1.5) ---")
out_rep = generator(
    prompt,
    max_new_tokens=30,
    do_sample=True,
    repetition_penalty=1.5,
    no_repeat_ngram_size=2,
    pad_token_id=generator.tokenizer.eos_token_id
)
print(out_rep[0]["generated_text"])
