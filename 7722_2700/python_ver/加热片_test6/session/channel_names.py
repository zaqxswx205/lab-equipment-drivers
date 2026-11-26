"""Update recording metadata without reconfiguring a device."""
from model.channels import clean_channel_name


def rename_channel(cfg, channel, name):
    name = clean_channel_name(name)
    item = next((item for item in cfg.channels if item.channel == channel), None)
    if item is None:
        raise ValueError(f'测温通道 {channel} 已不存在')
    item.name = name
