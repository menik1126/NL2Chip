#!/usr/bin/env python3

def test_fsm():
    """
    Test the FSM logic for sequence 1101 detection
    States: S=0, S1=1, S11=2, S110=3, Done=4
    """
    
    def next_state(current, data_bit):
        if current == 0:  # S
            return 1 if data_bit else 0
        elif current == 1:  # S1  
            return 2 if data_bit else 0
        elif current == 2:  # S11
            return 2 if data_bit else 3
        elif current == 3:  # S110
            return 4 if data_bit else 0
        elif current == 4:  # Done
            return 4
        else:
            return 0
    
    def output(state):
        return 1 if state == 4 else 0
    
    # Test sequence: 1101 should trigger detection
    test_sequence = "1101"
    state = 0  # Start in S
    print(f"Testing sequence: {test_sequence}")
    print(f"Initial state: {state}, output: {output(state)}")
    
    for i, bit in enumerate(test_sequence):
        data_bit = int(bit)
        state = next_state(state, data_bit)
        out = output(state)
        print(f"Step {i+1}: input={data_bit}, state={state}, output={out}")
    
    print(f"Final output: {output(state)} (should be 1)")
    assert output(state) == 1, "Sequence 1101 should be detected!"
    
    # Test that it stays in Done state
    for i in range(3):
        state = next_state(state, 0)  # Send some 0s
        out = output(state)
        print(f"After Done+{i+1}: state={state}, output={out}")
    
    assert output(state) == 1, "Should stay in Done state!"
    print("Test passed!")
    
    # Test reset behavior (implicitly tested by starting at state 0)
    
    # Test partial sequences that should NOT trigger
    print("\nTesting non-matching sequences:")
    
    test_cases = ["1100", "1011", "0110", "111"]
    for seq in test_cases:
        state = 0
        print(f"Testing: {seq}")
        for bit in seq:
            data_bit = int(bit)
            state = next_state(state, data_bit)
        final_output = output(state)
        print(f"  Final output: {final_output} (should be 0)")
        assert final_output == 0, f"Sequence {seq} should NOT be detected!"
    
    print("All tests passed!")

if __name__ == "__main__":
    test_fsm()