import React from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";

export default function ProtectedRoute({ children }) {
  const { user } = useAuth();
  if (user === null) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#F9F9F8]" data-testid="auth-loading">
        <div className="text-overline">Loading…</div>
      </div>
    );
  }
  if (user === false) return <Navigate to="/auth" replace />;
  return children;
}
