/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScriptDivisionProposal } from './ScriptDivisionProposal';
import type { ScriptExtractionProposal } from './ScriptExtractionProposal';
import type { ScriptTextProposal } from './ScriptTextProposal';
/**
 * Expose a refreshable proposal and its actual original-project charge without private evidence.
 */
export type ScriptCandidateRead = {
    id: string;
    project_id: string;
    chapter_id: string;
    shot_id: (string | null);
    operation: 'optimize' | 'simplify' | 'divide' | 'extract_shot';
    source_field: 'raw_text' | 'condensed_text';
    source_revision: number;
    source_hash: string;
    source_text: string;
    status: string;
    decision: 'pending' | 'adopted' | 'dismissed';
    model_name: string;
    authorized_max_minor: string;
    result: (ScriptTextProposal | ScriptDivisionProposal | ScriptExtractionProposal | null);
    output_hash: (string | null);
    source_current: boolean;
    can_adopt: boolean;
    message: string;
};

