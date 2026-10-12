// Reference digital gate only. No actuator, board pin or voltage qualification.
module orionq_fast_gate #(
    parameter PORTS = 8,
    parameter PHYSICAL_ENABLE = 0
) (
    input wire clk,
    input wire resetn,
    input wire cfg_valid,
    output wire cfg_ready,
    input wire [PORTS-1:0] cfg_mask,
    input wire [31:0] cfg_period_cycles,
    input wire [31:0] cfg_width_cycles,
    input wire [31:0] cfg_watchdog_cycles,
    input wire arm,
    input wire disarm,
    input wire clear_fault,
    input wire interlock_ok,
    input wire configuration_verified,
    input wire session_permit,
    input wire clock_locked,
    input wire heartbeat,
    input wire external_fault,
    output reg armed,
    output reg fault_latched,
    output reg [7:0] fault_flags,
    output wire [PORTS-1:0] gate_out,
    output wire physical_locked
);
    reg configured;
    reg [PORTS-1:0] mask;
    reg [31:0] period_cycles, width_cycles, watchdog_cycles;
    reg [31:0] phase_cycles, silent_cycles;
    wire prerequisites = interlock_ok && configuration_verified && session_permit && clock_locked && !external_fault;
    assign physical_locked = PHYSICAL_ENABLE == 0;
    assign cfg_ready = !armed && !fault_latched;
    // Fail low combinatorially on guard loss, including between clock edges.
    // Asynchronous guard inputs require a qualified CDC/interlock wrapper in a
    // real board; this Boolean logic is not a certified physical safety system.
    assign gate_out = PHYSICAL_ENABLE != 0 && resetn && armed && !fault_latched
                      && prerequisites && phase_cycles < width_cycles ? mask : {PORTS{1'b0}};

    task latch_fault;
        input [7:0] reason;
        begin
            armed <= 0;
            fault_latched <= 1;
            fault_flags <= fault_flags | reason;
            phase_cycles <= 0;
            silent_cycles <= 0;
        end
    endtask

    always @(posedge clk) begin
        if (!resetn) begin
            configured <= 0;
            armed <= 0;
            fault_latched <= 0;
            fault_flags <= 0;
            mask <= 0;
            phase_cycles <= 0;
            silent_cycles <= 0;
            period_cycles <= 1;
            width_cycles <= 0;
            watchdog_cycles <= 1;
        end else begin
            if (external_fault) latch_fault(8'd16);
            else if (armed && !prerequisites) latch_fault((!interlock_ok ? 8'd1 : 0)
                | (!configuration_verified ? 8'd2 : 0) | (!session_permit ? 8'd4 : 0)
                | (!clock_locked ? 8'd8 : 0));
            else if (disarm) begin
                armed <= 0;
                phase_cycles <= 0;
                silent_cycles <= 0;
            end else if (armed) begin
                if (!prerequisites) latch_fault((!interlock_ok ? 8'd1 : 0)
                    | (!configuration_verified ? 8'd2 : 0) | (!session_permit ? 8'd4 : 0)
                    | (!clock_locked ? 8'd8 : 0) | (external_fault ? 8'd16 : 0));
                else if (cfg_valid) latch_fault(8'd2);
                else if (!heartbeat && silent_cycles >= watchdog_cycles - 1) latch_fault(8'd32);
                else begin
                    silent_cycles <= heartbeat ? 0 : silent_cycles + 1;
                    phase_cycles <= phase_cycles + 1 >= period_cycles ? 0 : phase_cycles + 1;
                end
            end else if (fault_latched) begin
                if (clear_fault && !arm && prerequisites) begin
                    fault_latched <= 0;
                    fault_flags <= 0;
                    // Clear is not arm, and configuration must be reloaded.
                    configured <= 0;
                end
            end else if (cfg_valid) begin
                if (cfg_mask == 0 || cfg_period_cycles == 0 || cfg_width_cycles == 0
                        || cfg_width_cycles > cfg_period_cycles || cfg_watchdog_cycles == 0) latch_fault(8'd2);
                else begin
                    mask <= cfg_mask;
                    period_cycles <= cfg_period_cycles;
                    width_cycles <= cfg_width_cycles;
                    watchdog_cycles <= cfg_watchdog_cycles;
                    configured <= 1;
                end
            end else if (arm) begin
                if (PHYSICAL_ENABLE == 0) latch_fault(8'd64);
                else if (!configured || !prerequisites) latch_fault((!configured || !configuration_verified ? 8'd2 : 0)
                    | (!interlock_ok ? 8'd1 : 0) | (!session_permit ? 8'd4 : 0)
                    | (!clock_locked ? 8'd8 : 0) | (external_fault ? 8'd16 : 0));
                else begin
                    armed <= 1;
                    phase_cycles <= 0;
                    silent_cycles <= 0;
                end
            end
        end
    end
endmodule
