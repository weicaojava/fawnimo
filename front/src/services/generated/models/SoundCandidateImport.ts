/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Register an actual project WAV as a candidate, without adopting it anywhere.
 */
export type SoundCandidateImport = {
    request_key: string;
    chapter_id: string;
    media_id: string;
    expected_sha256: string;
    kind: 'voice' | 'effect' | 'music';
    name: string;
};

