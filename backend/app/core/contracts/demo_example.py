"""Original inert example source shared by text preparation and local geometry installation; no model or private data."""

def original_demo_document(project_id: str) -> list[dict]:
    """Build only self-authored text and deterministic resource IDs for the current template."""
    chapter = project_id + "-chapter"
    story = [
        ("雾中的小岛", "透明小岛静置于深蓝桌面，岛中央的空心方块仍未亮起。", "ELS", "STATIC"),
        ("第一缕光", "一颗金色光点从画面左侧进入，在方块的左边停下。", "MS", "PAN"),
        ("轻轻触碰", "光点触碰方块，方块的第一条边线亮起。", "CU", "STATIC"),
        ("沿边行走", "金色光点沿方块外缘缓慢绕行，留下连续柔和的光线。", "MCU", "TRACK"),
        ("小岛回应", "方块亮起后，三枚小三角片依次发出淡蓝微光。", "MS", "DOLLY_OUT"),
        ("光的汇合", "金色光点回到方块上方，与三枚三角片的微光汇合。", "LS", "STATIC"),
        ("点亮小岛", "全岛保持温暖微光，镜头缓慢拉远，画面安静收束。", "ELS", "DOLLY_OUT"),
    ]
    provenance = "纯自作几何光影练习文本；未使用用户原片或第三方素材；没有模型调用或已生成成片。"
    description = provenance + "示例可自由编辑；重新建立示例会新建工程，保留所有旧示例和编辑。"
    project = {"id": project_id, "name": "体验示例 · 点亮小岛", "description": description,
               "style": "动漫3D", "visual_style": "动漫", "seed": 20261029,
               "unify_style": True, "default_video_ratio": "9:16"}
    raw = "《点亮小岛》\n" + provenance + "\n\n" + "\n".join(f"{i}. {title}：{text}" for i, (title, text, _, _) in enumerate(story, 1))
    steps = [
        {"collection": "projects", "body": project},
        {"collection": "chapters", "body": {"id": chapter, "project_id": project_id, "index": 1,
            "title": "第一章 · 光的旅程", "summary": provenance, "raw_text": raw,
            "condensed_text": "金色光点点亮几何小岛。", "storyboard_count": 7, "status": "draft"}},
    ]
    for index, (title, text, camera, movement) in enumerate(story, 1):
        shot = f"{project_id}-shot-{index}"
        steps.append({"collection": "shots", "body": {"id": shot, "chapter_id": chapter,
            "index": index, "title": title, "status": "pending", "skip_extraction": False,
            "script_excerpt": text}})
        prefix = f"原创几何光影练习，竖屏9:16，透明小岛、空心方块与金色光点，镜头{index}：{text}"
        steps.append({"collection": "shot-details", "body": {"id": shot, "camera_shot": camera,
            "angle": "EYE_LEVEL", "movement": movement, "duration": 4,
            "atmosphere": "安静、克制、暖光与冷蓝背景", "mood_tags": ["安静", "希望"],
            "action_beats": [text], "has_bgm": False,
            "first_frame_prompt": prefix + "。首帧：展示动作发生前的稳定构图。",
            "last_frame_prompt": prefix + "。尾帧：展示动作完成后的稳定构图。",
            "key_frame_prompt": prefix + "。关键帧：保留方块、光点和小岛的空间关系。"}})
    return steps
