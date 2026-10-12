`timescale 1ns/1ps
module tb_fast_gate;
    reg clk=0, resetn=0;
    always #5 clk=~clk;
    reg cfg_valid=0, arm=0, disarm=0, clear_fault=0;
    reg interlock=1, verified=1, permit=1, locked=1, heartbeat=1, external_fault=0;
    reg [1:0] mask=2'b11;
    reg [31:0] period_cycles=4, width_cycles=2, watchdog=5;
    wire ready_safe, armed_safe, fault_safe, physical_safe;
    wire ready_sim, armed_sim, fault_sim, physical_sim;
    wire [7:0] flags_safe, flags_sim;
    wire [1:0] out_safe,out_sim;
    integer idx, checks=0;
    orionq_fast_gate #(.PORTS(2)) safe_dut(clk,resetn,cfg_valid,ready_safe,mask,period_cycles,width_cycles,watchdog,
        arm,disarm,clear_fault,interlock,verified,permit,locked,heartbeat,external_fault,
        armed_safe,fault_safe,flags_safe,out_safe,physical_safe);
    // This instance exists only in this RTL simulator, never a delivered board.
    orionq_fast_gate #(.PORTS(2),.PHYSICAL_ENABLE(1)) sim_dut(clk,resetn,cfg_valid,ready_sim,mask,period_cycles,width_cycles,watchdog,
        arm,disarm,clear_fault,interlock,verified,permit,locked,heartbeat,external_fault,
        armed_sim,fault_sim,flags_sim,out_sim,physical_sim);
    task reset_fixture;
        begin
            resetn=0; arm=0; cfg_valid=0; clear_fault=0; disarm=0;
            interlock=1; verified=1; permit=1; locked=1; heartbeat=1; external_fault=0;
            period_cycles=4; width_cycles=2; watchdog=5;
            repeat(3) @(negedge clk); resetn=1;
            if(out_sim!==0 || out_safe!==0 || armed_sim || armed_safe) $fatal(1,"reset armed a port");
            checks=checks+1;
        end
    endtask
    task configure_and_arm;
        begin
            cfg_valid=1; @(negedge clk); cfg_valid=0;
            arm=1; @(negedge clk); arm=0;
            if(!armed_sim || out_safe!==0 || armed_safe || !fault_safe || flags_safe!=64) $fatal(1,"physical lock or explicit arm failed");
            checks=checks+1;
        end
    endtask
    task check_fault;
        input [7:0] expected;
        begin
            if(out_sim!==0) $fatal(1,"guard loss did not immediately close output");
            @(negedge clk);
            if(armed_sim || !fault_sim || (flags_sim & expected)!=expected) $fatal(1,"fault not latched");
            checks=checks+1;
        end
    endtask
    initial begin
        reset_fixture();
        if(!physical_safe || physical_sim) $fatal(1,"physical profile metadata incorrect");
        configure_and_arm();
        for(idx=0;idx<8;idx=idx+1) begin
            if(out_sim !== (idx%4<2 ? 2'b11 : 2'b00)) $fatal(1,"period/width wrong");
            @(negedge clk);
        end
        checks=checks+1;
        interlock=0; #1; check_fault(1);
        interlock=1; arm=1; repeat(2) @(negedge clk); arm=0;
        if(armed_sim || !fault_sim) $fatal(1,"interlock restoration rearmed output");
        clear_fault=1; @(negedge clk); clear_fault=0;
        if(armed_sim || fault_sim) $fatal(1,"clear should not arm");
        arm=1; @(negedge clk); arm=0;
        if(armed_sim || !fault_sim || !(flags_sim&2)) $fatal(1,"clear bypassed configuration reload");
        checks=checks+1;
        reset_fixture(); configure_and_arm(); permit=0; #1; check_fault(4);
        reset_fixture(); configure_and_arm(); verified=0; #1; check_fault(2);
        reset_fixture(); configure_and_arm(); locked=0; #1; check_fault(8);
        reset_fixture(); configure_and_arm(); external_fault=1; #1; check_fault(16);
        reset_fixture(); configure_and_arm(); heartbeat=0; repeat(5) @(negedge clk);
        if(armed_sim || !fault_sim || !(flags_sim&32) || out_sim!==0) $fatal(1,"watchdog failed");
        checks=checks+1;
        reset_fixture(); configure_and_arm(); cfg_valid=1; @(negedge clk); cfg_valid=0;
        if(armed_sim || !fault_sim || !(flags_sim&2)) $fatal(1,"live configuration mutation accepted");
        checks=checks+1;
        reset_fixture(); width_cycles=5; cfg_valid=1; @(negedge clk); cfg_valid=0;
        if(!fault_sim || armed_sim || out_sim!==0) $fatal(1,"invalid timing accepted");
        checks=checks+1;
        reset_fixture(); configure_and_arm(); interlock=0; disarm=1; #1; check_fault(1);
        reset_fixture(); configure_and_arm(); disarm=1; @(negedge clk); disarm=0;
        if(armed_sim || out_sim!==0) $fatal(1,"explicit disarm failed");
        checks=checks+1;
        $display("RTL_FAST_GATE_PASS checks=%0d physical_actions=0",checks);
        $finish;
    end
    always @(negedge clk) if(out_safe!==0) $fatal(1,"default module drove physical output");
    initial begin #100000; $fatal(1,"global gate simulation timeout"); end
endmodule
