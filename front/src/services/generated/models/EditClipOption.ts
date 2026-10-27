/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditTakeOption } from './EditTakeOption';
/**
 * One actual planned shot and its historical take choices.
 */
export type EditClipOption = {
    shot_id: string;
    title: string;
    index: number;
    duration_ms: number;
    takes: Array<EditTakeOption>;
};

