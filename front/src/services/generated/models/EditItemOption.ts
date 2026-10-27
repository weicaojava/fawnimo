/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Original immutable story item and allowable mapped shots.
 */
export type EditItemOption = {
    kind: 'beat' | 'dialogue';
    item_id: string;
    required: boolean;
    text: string;
    speaker_id: (string | null);
    shot_ids: Array<string>;
};

