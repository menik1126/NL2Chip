# Generate XOR reduction for 100 bits using extractLsb
def gen_xor_100_lean():
    # Generate let bindings for each bit
    lets = []
    for i in range(100):
        lets.append(f"  let bit{i} := Signal.map (fun x => x.extractLsb {i} {i}) input")
    
    # Generate XOR chain
    xor_chain = " ^^^ ".join([f"bit{i}" for i in range(100)])
    
    return "\n".join(lets) + "\n  " + xor_chain

print(gen_xor_100_lean())
