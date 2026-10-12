// OrionQ reference RTL, original project code. No vendor IP or board binding.
// See abi.md. Integer timestamps are units, not a claim of physical resolution.
module orionq_correlation #(
    parameter MAX_EVENTS = 256,
    parameter MAX_BINS = 257
) (
    input wire clk,
    input wire resetn,
    input wire [511:0] cfg_tdata,
    input wire cfg_tvalid,
    output wire cfg_tready,
    input wire [127:0] s_axis_tdata,
    input wire s_axis_tvalid,
    input wire s_axis_tlast,
    output wire s_axis_tready,
    output wire [127:0] m_axis_tdata,
    output wire m_axis_tvalid,
    output wire m_axis_tlast,
    input wire m_axis_tready,
    output wire [63:0] m_axis_request_id,
    output wire [63:0] m_axis_pairs,
    output wire busy
);
    localparam IDLE=0, CLEAR=1, INGEST=2, CORRELATE=3, OUTPUT=4;
    reg [2:0] state;
    reg [63:0] request_id, bin_width_ps, radius_ps, pair_budget, pair_count;
    reg signed [63:0] delay_ps;
    reg signed [31:0] channel_a, channel_b;
    reg [31:0] event_count, bin_count, cycle_budget, elapsed_cycles;
    reg [31:0] input_index, clear_index, index_a, index_b, output_index;
    reg [31:0] status, ingest_status;
    reg signed [63:0] previous_timestamp;
    reg signed [63:0] timestamps [0:MAX_EVENTS-1];
    reg signed [31:0] channels [0:MAX_EVENTS-1];
    reg [63:0] histogram [0:MAX_BINS-1];

    wire [63:0] cfg_width = cfg_tdata[319:256];
    wire [63:0] cfg_radius = cfg_tdata[383:320];
    wire [64:0] cfg_bins = cfg_width == 0 ? 65'd0 : ({1'b0,cfg_radius} << 1) / cfg_width + 1;
    wire signed [63:0] event_timestamp = s_axis_tdata[63:0];
    wire signed [31:0] event_channel = s_axis_tdata[95:64];
    wire [31:0] event_flags = s_axis_tdata[127:96];
    wire signed [65:0] delta = {{2{timestamps[index_b][63]}},timestamps[index_b]}
                             - {{2{timestamps[index_a][63]}},timestamps[index_a]}
                             - {{2{delay_ps[63]}},delay_ps};
    wire signed [65:0] radius_ext = {2'b00,radius_ps};
    wire [65:0] bin_index = (delta + radius_ext) / {2'b00,bin_width_ps};
    wire [31:0] event_status = (event_flags & 32'd3)
                            | ((event_flags & ~32'd3) != 0 || event_timestamp < 0 || event_channel == 0 ? 32'd32 : 32'd0)
                            | (input_index != 0 && event_timestamp < previous_timestamp ? 32'd64 : 32'd0);

    assign cfg_tready = state == IDLE;
    assign s_axis_tready = state == INGEST;
    assign m_axis_tvalid = state == OUTPUT;
    assign m_axis_tlast = status != 0 || output_index + 1 == bin_count;
    assign m_axis_tdata = status != 0 ? {status,32'hffffffff,64'd0}
                                     : {32'd0,output_index,histogram[output_index]};
    assign m_axis_request_id = request_id;
    assign m_axis_pairs = status == 0 ? pair_count : 64'd0;
    assign busy = state != IDLE;

    task reject;
        input [31:0] reason;
        begin
            status <= reason;
            pair_count <= 0;
            output_index <= 0;
            state <= OUTPUT;
        end
    endtask

    task advance_pair;
        begin
            if (index_b + 1 == event_count) begin
                index_b <= 0;
                if (index_a + 1 == event_count) begin
                    output_index <= 0;
                    state <= OUTPUT;
                end else index_a <= index_a + 1;
            end else index_b <= index_b + 1;
        end
    endtask

    always @(posedge clk) begin
        if (!resetn) begin
            state <= IDLE;
            status <= 0;
            request_id <= 0;
            pair_count <= 0;
            output_index <= 0;
            bin_count <= 0;
            bin_width_ps <= 1;
            radius_ps <= 1;
            delay_ps <= 0;
            index_a <= 0;
            index_b <= 0;
            elapsed_cycles <= 0;
        end else begin
            if (state == CLEAR || state == INGEST || state == CORRELATE) begin
                elapsed_cycles <= elapsed_cycles + 1;
                if (elapsed_cycles >= cycle_budget - 1) reject(32'd128);
                else begin
                    case (state)
                        CLEAR: begin
                            histogram[clear_index] <= 0;
                            if (clear_index + 1 == bin_count) begin
                                input_index <= 0;
                                if (event_count == 0) begin
                                    output_index <= 0;
                                    state <= OUTPUT;
                                end else state <= INGEST;
                            end else clear_index <= clear_index + 1;
                        end
                        INGEST: if (s_axis_tvalid) begin
                            timestamps[input_index] <= event_timestamp;
                            channels[input_index] <= event_channel;
                            previous_timestamp <= event_timestamp;
                            ingest_status <= ingest_status | event_status;
                            if (s_axis_tlast || input_index + 1 == event_count) begin
                                if (s_axis_tlast != (input_index + 1 == event_count)) reject(ingest_status | event_status | 32'd8);
                                else if ((ingest_status | event_status) != 0) reject(ingest_status | event_status);
                                else begin
                                    index_a <= 0;
                                    index_b <= 0;
                                    state <= CORRELATE;
                                end
                            end else input_index <= input_index + 1;
                        end
                        CORRELATE: begin
                            if (channels[index_a] == channel_a && channels[index_b] == channel_b
                                    && delta >= -radius_ext && delta <= radius_ext) begin
                                if (pair_count >= pair_budget) reject(32'd16);
                                else if (bin_index >= bin_count) reject(32'd32);
                                else begin
                                    histogram[bin_index] <= histogram[bin_index] + 1;
                                    pair_count <= pair_count + 1;
                                    advance_pair();
                                end
                            end else advance_pair();
                        end
                    endcase
                end
            end else case (state)
                IDLE: if (cfg_tvalid) begin
                    request_id <= cfg_tdata[127:64];
                    event_count <= cfg_tdata[159:128];
                    channel_a <= cfg_tdata[191:160];
                    channel_b <= cfg_tdata[223:192];
                    cycle_budget <= cfg_tdata[255:224];
                    bin_width_ps <= cfg_width;
                    radius_ps <= cfg_radius;
                    delay_ps <= cfg_tdata[447:384];
                    pair_budget <= cfg_tdata[511:448];
                    bin_count <= cfg_bins[31:0];
                    pair_count <= 0;
                    elapsed_cycles <= 0;
                    clear_index <= 0;
                    output_index <= 0;
                    ingest_status <= 0;
                    status <= 0;
                    if (cfg_tdata[31:0] != 32'h524f4351 || cfg_tdata[47:32] != 16'd1 || cfg_tdata[63:48] != 0
                            || cfg_tdata[159:128] > MAX_EVENTS
                            || cfg_tdata[191:160] == 0 || cfg_tdata[223:192] == 0
                            || cfg_tdata[191:160] == cfg_tdata[223:192]
                            || cfg_tdata[255:224] == 0 || cfg_width == 0 || cfg_width[63]
                            || cfg_radius == 0 || cfg_radius[63] || cfg_radius % cfg_width != 0
                            || cfg_bins > MAX_BINS || cfg_tdata[447:384] == 64'h8000000000000000
                            || cfg_tdata[511:448] == 0 || cfg_tdata[511:448] > 64'd10000000) reject(32'd4);
                    else state <= CLEAR;
                end
                OUTPUT: if (m_axis_tready) begin
                    if (m_axis_tlast) state <= IDLE;
                    else output_index <= output_index + 1;
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
