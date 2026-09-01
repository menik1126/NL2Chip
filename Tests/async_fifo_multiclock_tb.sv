`timescale 1ns/1ps

module async_fifo_multiclock_tb;
  logic write_clock = 0;
  logic read_clock = 0;
  logic write_reset = 1;
  logic read_reset = 1;
  logic write_increment = 0;
  logic [7:0] write_data = 0;
  logic read_increment = 0;
  logic write_full;
  logic [7:0] read_data;
  logic read_empty;

  always #4 write_clock = ~write_clock;
  always #6 read_clock = ~read_clock;

  Tests_AsyncFifoElab_fifoTop dut (
    ._gen_writeIncrement(write_increment),
    ._gen_writeData(write_data),
    ._gen_readIncrement(read_increment),
    .read_clock(read_clock),
    .read_reset(read_reset),
    .write_clock(write_clock),
    .write_reset(write_reset),
    .write_full(write_full),
    .read_data(read_data),
    .read_empty(read_empty)
  );

  task automatic write_word(input logic [7:0] value);
    @(negedge write_clock);
    write_increment = 1;
    write_data = value;
    @(negedge write_clock);
    write_increment = 0;
  endtask

  task automatic reset_fifo;
    write_reset = 1;
    read_reset = 1;
    write_increment = 0;
    read_increment = 0;
    repeat (2) @(posedge write_clock);
    repeat (2) @(posedge read_clock);
    write_reset = 0;
    read_reset = 0;
  endtask

  initial begin
    reset_fifo();
    if (write_full !== 1'b0 || read_empty !== 1'b1)
      $fatal(1, "bad FIFO reset state: full=%b empty=%b", write_full, read_empty);

    write_word(8'hA5);
    #1;
    if (read_data !== 8'hA5)
      $fatal(1, "FWFT data mismatch: %h", read_data);

    repeat (3) @(posedge read_clock);
    #1;
    if (read_empty !== 1'b0)
      $fatal(1, "read domain did not observe write pointer");

    @(negedge read_clock);
    read_increment = 1;
    @(negedge read_clock);
    read_increment = 0;
    #1;
    if (read_empty !== 1'b1)
      $fatal(1, "FIFO did not become empty after read");

    reset_fifo();
    for (int value = 1; value <= 8; value++)
      write_word(value[7:0]);
    #1;
    if (write_full !== 1'b1)
      $fatal(1, "FIFO full flag did not assert at depth");

    $display("ASYNC_FIFO_MULTICLOCK_SV_PASS");
    $finish;
  end
endmodule
