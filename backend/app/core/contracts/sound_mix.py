"""Bounded music placement and confirmed-voice envelope rules shared by publication and the owned renderer."""
MUSIC_PROFILE='sound_music_v1'
ATTACK_MS=50
RELEASE_MS=150


def checked_music_options(value):
    """Accept only explicit looping or attenuation, without expressions, stretch rates or implicit output extension."""
    if value is None:return None
    if type(value) is not dict or not value or set(value)-{'loop_until_ms','voice_duck_db'}:
        raise ValueError('Invalid explicit music options')
    result={key:item for key,item in value.items() if item is not None}
    if not result:raise ValueError('Music options require an explicit operation')
    end=result.get('loop_until_ms')
    if end is not None and (type(end) is not int or not 10<=end<=300000 or end%10):
        raise ValueError('Music loop end requires the bounded10ms output clock')
    gain=result.get('voice_duck_db')
    if gain is not None and (type(gain) is not int or not -30<=gain<=-3):
        raise ValueError('Music ducking requires an integer attenuation between-30 and-3dB')
    return result


def music_timeline_end(track,kind,*,duration=None):
    """Extend only an explicit music loop, preserving the original trimmed source interval and all old placement defaults."""
    base=track['timeline_start_ms']+track['source_out_ms']-track['source_in_ms']
    options=checked_music_options(track.get('music'))
    if options is not None:
        if kind!='music' or track.get('dialogue_id') is not None:
            raise ValueError('Looping and ducking are available only for music candidates')
        end=options.get('loop_until_ms',base)
        if 'loop_until_ms' in options and end<=base:
            raise ValueError('A loop must explicitly extend its unmodified source placement')
    else:end=base
    if duration is not None and end>duration:raise ValueError('Music exceeds the locked output clock')
    return end


def confirmed_voice_windows(tracks,duration):
    """Use only verified, unmuted, explicitly confirmed original-dialogue coverage; never infer speech from pictures or music."""
    if type(duration) is not int or not 100<=duration<=300000 or type(tracks) is not list or len(tracks)>64:
        raise ValueError('Invalid bounded music clock')
    windows=[]
    for track in tracks:
        cue=track.get('dialogue')
        if (track.get('candidate',{}).get('kind')!='voice' or track.get('muted') is not False
                or track.get('content_confirmed') is not True or type(track.get('gain_db')) is not int
                or track['gain_db'] < -18 or type(cue) is not dict
                or type(track.get('dialogue_peak_pcm16')) is not int or track['dialogue_peak_pcm16']<=32):
            continue
        start,end=cue.get('start_ms'),cue.get('end_ms')
        if (type(start) is not int or type(end) is not int or not 0<=start<end<=duration
                or track['timeline_start_ms']>start or track['timeline_end_ms']<end):
            continue
        windows.append([start,end])
    merged=[]
    for start,end in sorted(windows):
        if merged and start<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],end)
        else:merged.append([start,end])
    return merged


def music_mix_policy(tracks,duration):
    """Bind opt-in music rules and verified voice windows into the immutable publication, leaving old payloads untouched."""
    if len(tracks)>64:raise ValueError('Too many sound tracks')
    options=[];ducking=False
    for track in tracks:
        value=checked_music_options(track.get('music'))
        if value is not None:
            music_timeline_end(track,track['candidate']['kind'])
            options.append(value)
            if track.get('muted') is False and 'voice_duck_db' in value:ducking=True
    if not options:return None
    windows=confirmed_voice_windows(tracks,duration)
    return {'profile':MUSIC_PROFILE,'voice_windows_ms':windows,'attack_ms':ATTACK_MS,'release_ms':RELEASE_MS,
            'voice_source':'explicit confirmed original-dialogue audio tracks; no speech detection',
            'ducking_requested':ducking}


def music_duck_expression(windows,gain_db):
    """Generate only numeric bounded sample-clock arithmetic, with overlapping voice ramps taking the strongest attenuation once."""
    checked_music_options({'voice_duck_db':gain_db})
    if type(windows) is not list or not 1<=len(windows)<=64:raise ValueError('No bounded confirmed voice windows')
    weight='0'
    for item in windows:
        if type(item) is not list or len(item)!=2 or any(type(value) is not int for value in item) or not 0<=item[0]<item[1]<=300000:
            raise ValueError('Invalid confirmed voice interval')
        start,end=item
        ramp=f'max(0,min(1,min((t-({(start-ATTACK_MS)/1000:.3f}))/0.050,(({(end+RELEASE_MS)/1000:.3f})-t)/0.150)))'
        weight=f'max({weight},{ramp})'
    factor=10**(gain_db/20)
    return f'(1-({1-factor:.12f})*({weight}))'
