"""Explicit registration, replacement and capability admission of technology adapters."""
from .contracts import AcquisitionSource, ProtocolAdapter, ProtocolConfig, ProtocolError


class TechnologyRegistry:
    def __init__(self):
        self.adapters = {}
        self.sources = {}

    def register(self, adapter: ProtocolAdapter, *, replace=False):
        if not isinstance(adapter, ProtocolAdapter):
            raise ProtocolError('invalid_adapter', 'ProtocolAdapter contract required')
        if adapter.technology_id in self.adapters and not replace:
            raise ProtocolError('duplicate_adapter', adapter.technology_id)
        self.adapters[adapter.technology_id] = adapter

    def register_source(self, source: AcquisitionSource, *, replace=False):
        if not isinstance(source, AcquisitionSource):
            raise ProtocolError('invalid_source', 'AcquisitionSource contract required')
        if source.source_id in self.sources and not replace:
            raise ProtocolError('duplicate_source', source.source_id)
        self.sources[source.source_id] = source

    def validate(self, config: ProtocolConfig, topology):
        from ..control_plane.channels import ChannelPair
        if set(config.technologies)&{'quantum_memory','purification'} and set(config.technologies)&{'tf_qkd','satellite_doppler','satellite_isl'}:
            raise ProtocolError('unsupported_composition','memory/purification resource flow is implemented with swapping; TF/satellite compositions require an additional driver')
        if 'satellite_isl' in config.technologies and len(config.technologies)!=1:
            raise ProtocolError('unsupported_composition','ISL detector BBM92 cannot compose with ground/swapping drivers without quantum storage and relay operations')
        records=[topology.node(node_id).telemetry.get('communicator') for node_id in config.path]
        present=[r for r in records if r]
        if any(r['physical_type']=='satellite' for r in present) and not set(config.technologies)&{'satellite_doppler','satellite_isl','orbital_relay'}:
            raise ProtocolError('incompatible_node','orbital devices require the implemented free-space satellite driver')
        modes={r['integration']['mode'] for r in present}
        if len(modes)>1:
            raise ProtocolError('incompatible_sources','all devices in a session require compatible acquisition origins')
        if modes=={'measurement_file'} and len({r['integration']['service_address'] for r in present})!=1:
            raise ProtocolError('incompatible_sources','one command-bound measurement file required per session')
        if 'tf_qkd' in config.technologies and len(records)==3:
            for index,record in enumerate(records):
                if record and record['configuration'].get('tf_qkd',{}).get('device_role')!=('central_measurement' if index==1 else 'sender'):
                    raise ProtocolError('incompatible_node','TF requires two senders and one central measurement device')
            if records[0] and records[2]:
                left,right=(r['configuration']['tf_qkd'] for r in (records[0],records[2]))
                if any(left[k]!=right[k] for k in ('pulse_rate_hz','signal_mu','decoy_nu')):
                    raise ProtocolError('incompatible_node','implemented symmetric SNS-TF requires matching sender pulse rates and intensities')
        for node_id in config.path:
            record=topology.node(node_id).telemetry.get('communicator')
            if record and record['integration']['mode']=='physical':
                raise ProtocolError('adapter_unvalidated',f'{node_id}: no validated physical driver')
        for index, ident in enumerate(config.link_ids):
            try:
                link = next(x for x in topology.links if x.link_id == ident)
            except StopIteration:
                raise ProtocolError('missing_link', ident) from None
            if {link.left_node_id, link.right_node_id} != set(config.path[index:index + 2]):
                raise ProtocolError('invalid_path', f'{ident}: endpoints do not match path')
            if link.channels is None:
                raise ProtocolError('unpaired_channels', ident)
            pair=ChannelPair.from_dict(link.channels)
            pair.validate_link(link.link_id, link.right_node_id)
            if pair.quantum.protocol != 'quantum-operation-record-v1':
                raise ProtocolError('incompatible_channel','technology adapter requires quantum-operation-record-v1')
        for ident in config.technologies:
            if ident not in self.adapters:
                raise ProtocolError('adapter_unavailable', ident)
            self.adapters[ident].validate(config, topology)

    def describe(self):
        return {'adapters': {key: {'required_capabilities': list(value.required_capabilities),
                                   'implementation': type(value).__name__} for key, value in self.adapters.items()},
                'sources': {key: {'origin': value.origin, 'physical_validated': value.physical_validated,
                                 'implementation': type(value).__name__} for key, value in self.sources.items()}}


def default_registry():
    from .protocols import MemoryAdapter, PurificationAdapter, SatelliteAdapter, SwappingAdapter, TwinFieldAdapter
    from .isl import InterSatelliteAdapter
    from .orbital_relay import OrbitalRelayAdapter
    registry = TechnologyRegistry()
    for adapter in (SwappingAdapter(), TwinFieldAdapter(), PurificationAdapter(), SatelliteAdapter(), MemoryAdapter(), InterSatelliteAdapter(), OrbitalRelayAdapter()):
        registry.register(adapter)
    return registry
