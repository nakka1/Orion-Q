`timescale 1ns/1ps
module tb_correlation;
    reg clk=0, resetn=0;
    always #5 clk=~clk;
    reg [511:0] cfg=0;
    reg cfg_valid=0, in_valid=0, in_last=0, out_ready=0;
    reg [127:0] in_data=0;
    wire cfg_ready, in_ready, out_valid, out_last, busy;
    wire [127:0] out_data;
    wire [63:0] request_id, pairs;
    orionq_correlation dut(clk,resetn,cfg,cfg_valid,cfg_ready,in_data,in_valid,in_last,in_ready,
        out_data,out_valid,out_last,out_ready,request_id,pairs,busy);
    integer fd, cases, case_no, sent_count, expected_bins, idx, code, waits;
    integer total_frames=0, total_cycles=0;
    reg [31:0] expected_status;
    reg [63:0] expected_pairs, expected_counts[0:256];
    reg [127:0] events[0:255], held_data;
    integer lasts[0:255];
    reg held_last;
    reg [63:0] held_pairs, held_id;
    reg [4095:0] filename;
    always @(posedge clk) total_cycles <= total_cycles + 1;

    initial begin
        if (!$value$plusargs("vectors=%s",filename)) $fatal(1,"vectors path required");
        fd=$fopen(filename,"r");
        if (!fd) $fatal(1,"vectors file unavailable");
        code=$fscanf(fd,"%d",cases);
        if (code != 1) $fatal(1,"case count malformed");
        repeat(3) @(negedge clk);
        resetn=1;
        for(case_no=0;case_no<cases;case_no=case_no+1) begin
            code=$fscanf(fd,"%h %d %h %d %h",cfg,sent_count,expected_status,expected_bins,expected_pairs);
            if(code!=5 || sent_count>256 || expected_bins>257) $fatal(1,"case header malformed");
            for(idx=0;idx<sent_count;idx=idx+1) begin
                code=$fscanf(fd,"%h %d",events[idx],lasts[idx]);
                if(code!=2) $fatal(1,"event malformed");
            end
            for(idx=0;idx<expected_bins;idx=idx+1) begin
                code=$fscanf(fd,"%h",expected_counts[idx]);
                if(code!=1) $fatal(1,"expectation malformed");
            end
            while(!cfg_ready) @(negedge clk);
            cfg_valid=1;
            @(negedge clk); cfg_valid=0;
            for(idx=0;idx<sent_count;idx=idx+1) begin
                waits=0;
                while(!in_ready && !out_valid) begin
                    @(negedge clk); waits=waits+1;
                    if(waits>200000) $fatal(1,"input handshake timeout");
                end
                if(!out_valid) begin
                    in_data=events[idx]; in_last=lasts[idx]; in_valid=1;
                    @(negedge clk); in_valid=0;
                    repeat(idx%3) @(negedge clk);
                end
            end
            waits=0;
            while(!out_valid) begin
                @(negedge clk); waits=waits+1;
                if(waits>200000) $fatal(1,"result timeout");
            end
            held_data=out_data; held_last=out_last; held_pairs=pairs; held_id=request_id;
            repeat(7) begin
                @(negedge clk);
                if(!out_valid || out_data!==held_data || out_last!==held_last
                    || pairs!==held_pairs || request_id!==held_id || cfg_ready) $fatal(1,"output unstable under backpressure");
            end
            for(idx=0;idx<expected_bins;idx=idx+1) begin
                if(!out_valid || out_data[127:96]!==expected_status || out_data[63:0]!==expected_counts[idx]
                    || out_data[95:64] !== (expected_status==0 ? idx : 32'hffffffff)
                    || request_id!==cfg[127:64] || pairs!==expected_pairs
                    || out_last !== (idx+1==expected_bins)) begin
                    $display("case=%0d bin=%0d got=%h expected_status=%h expected_count=%h pairs=%0d/%0d",
                        case_no,idx,out_data,expected_status,expected_counts[idx],pairs,expected_pairs);
                    $fatal(1,"CPU/RTL result mismatch");
                end
                out_ready=1;
                @(negedge clk); out_ready=0;
                total_frames=total_frames+1;
            end
            if(busy || !cfg_ready) $fatal(1,"completion did not release ownership");
        end
        $fclose(fd);
        $display("RTL_CORRELATION_PASS cases=%0d frames=%0d cycles=%0d",cases,total_frames,total_cycles);
        $finish;
    end
    initial begin #100000000; $fatal(1,"global simulation timeout"); end
endmodule
