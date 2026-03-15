import { useEffect } from "react";
import { supabase } from "../lib/supabase";

type Props = {
  teamId?: string | null;
  onSessionUpdate: (payload: unknown) => void;
};

export function useSessionRealtime({ teamId, onSessionUpdate }: Props) {
  useEffect(() => {
    if (!teamId) {
      return;
    }
    const channel = supabase
      .channel("team-sessions")
      .on(
        "postgres_changes",
        {
          event: "UPDATE",
          schema: "public",
          table: "sessions",
          filter: `team_id=eq.${teamId}`
        },
        (payload) => onSessionUpdate(payload)
      )
      .subscribe();
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [onSessionUpdate, teamId]);
}

